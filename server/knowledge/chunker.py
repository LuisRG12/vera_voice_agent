"""Lectura y troceado de documentos clínicos.

El parseo trabaja sobre **bytes**, no sobre rutas: así la consola —que recibe un
archivo subido— y los scripts —que leen del disco— recorren exactamente el mismo
código. Dos parsers habrían sido una ocasión más para que las dos rutas diverjan.

El troceado respeta las secciones cuando el documento las declara (encabezados
markdown, estilos de Word) y cae a ventanas por frase cuando no —que es el caso
de casi cualquier PDF—.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

FORMATOS = (".md", ".txt", ".pdf", ".docx")


def _decode(raw: bytes) -> str:
    """Decodifica texto clínico venga como venga.

    `utf-8-sig` primero porque **consume el BOM**: sin eso, un archivo guardado
    desde el Bloc de notas empieza con `\\ufeff#` y su primer encabezado deja de
    reconocerse, así que el documento pierde su primera sección y sus citas salen
    sin referencia.

    `cp1252` después porque es lo que produce «Guardar como .txt» en un Windows en
    español.
    """
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_bytes(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext in (".md", ".txt"):
        return _decode(data)
    if ext == ".pdf":
        from pypdf import PdfReader

        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    if ext == ".docx":
        import docx

        # Los estilos de Word ("Heading 1", "Título 1") son estructura REAL, no
        # una heurística: se traducen a encabezados markdown para que el troceado
        # por secciones funcione igual que con un .md y la cita pueda decir de
        # qué sección salió la afirmación.
        out = []
        for par in docx.Document(io.BytesIO(data)).paragraphs:
            texto = par.text.strip()
            if not texto:
                continue
            estilo = (par.style.name if par.style is not None else "") or ""
            if estilo.lower().startswith(("heading", "título", "titulo")):
                nivel = "".join(c for c in estilo if c.isdigit()) or "1"
                out.append(f"{'#' * min(int(nivel), 6)} {texto}")
            else:
                out.append(texto)
        return "\n".join(out)
    raise ValueError(
        f"formato no soportado: {ext or '(sin extensión)'}. Admitidos: {', '.join(FORMATOS)}")


def parse_file(path: str) -> str:
    p = Path(path)
    return parse_bytes(p.name, p.read_bytes())


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []
    for line in text.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            if buf:
                sections.append((heading, "\n".join(buf).strip()))
                buf = []
            heading = m.group(2).strip()
        else:
            buf.append(line)
    if buf:
        sections.append((heading, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if b]


def _cola(texto: str, overlap: int) -> str:
    """Cola de solape que empieza en palabra completa.

    Cortar el solape como `texto[-overlap:]` —N caracteres a ciegas— parece
    inocuo y no lo es: sobre PDFs reales deja que **todos** los fragmentos
    empiecen a media palabra («ue resalta la severidad…», «ctor lineal de alta
    frecuencia…»). El embedding se calcula sobre ese texto, así que un fragmento
    que abre con un trozo de palabra inexistente queda con el vector corrido
    respecto al mismo pasaje bien cortado. Y la etiqueta de la cita, que se arma
    con sus primeras palabras, sale ilegible.

    Se prefiere empezar en frase; si no hay ninguna dentro de la ventana, en la
    siguiente palabra completa.
    """
    if len(texto) <= overlap:
        return texto
    trozo = texto[-overlap:]
    if (fin := re.search(r"(?<=[.?!])\s+", trozo)) is not None:
        return trozo[fin.end():]
    corte = trozo.find(" ")
    return trozo[corte + 1:] if corte >= 0 else ""


def _partir_larga(s: str, max_chars: int) -> list[str]:
    """Parte una frase más larga que la ventana, por palabras.

    Hace falta porque los PDFs académicos traen tablas y listas que el extractor
    entrega sin puntuación: sin esto salen fragmentos de miles de caracteres, y
    unos pocos de esos desbordan la ventana de contexto del modelo.
    """
    if len(s) <= max_chars:
        return [s]
    trozos, cur = [], ""
    for palabra in s.split():
        if cur and len(cur) + len(palabra) + 1 > max_chars:
            trozos.append(cur)
            cur = palabra
        else:
            cur = f"{cur} {palabra}".strip()
    if cur:
        trozos.append(cur)
    return trozos


def _windows(text: str, max_chars: int, overlap: int) -> list[str]:
    frases = re.split(r"(?<=[.?!])\s+", text.replace("\n", " ").strip())
    sents = [t for s in frases for t in _partir_larga(s, max_chars)]
    chunks: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > max_chars:
            chunks.append(cur.strip())
            cur = (_cola(cur, overlap) + " " + s).strip()
        else:
            cur = (cur + " " + s).strip()
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def _etiqueta(fragmento: str, i: int, total: int) -> str:
    """Referencia para un fragmento sin encabezado propio.

    Un PDF no trae encabezados markdown, así que sus citas saldrían como
    «documento.pdf §» —sin sección—. Antes que inventar una estructura con
    heurísticas frágiles (en un protocolo, «Orina turbia o con mal olor» parece
    un título y es una viñeta), se cita la posición y las primeras palabras: es
    verificable y le permite al equipo clínico encontrar el pasaje.
    """
    inicio = " ".join(fragmento.split()[:6])
    return f"parte {i}/{total} · {inicio}…" if inicio else f"parte {i}/{total}"


def chunk_document(text: str, max_chars: int = 700, overlap: int = 120) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for heading, body in _split_sections(text):
        for w in _windows(body, max_chars, overlap):
            out.append((heading, w))

    sin_seccion = [i for i, (h, _) in enumerate(out) if not h]
    if sin_seccion:
        for n, i in enumerate(sin_seccion, start=1):
            out[i] = (_etiqueta(out[i][1], n, len(sin_seccion)), out[i][1])

    if not out and text.strip():
        ventanas = _windows(text, max_chars, overlap)
        out = [(_etiqueta(w, i, len(ventanas)), w) for i, w in enumerate(ventanas, start=1)]
    return out
