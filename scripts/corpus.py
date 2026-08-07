"""Construye la base de conocimiento a partir del corpus clínico del reto.

    uv run scripts/corpus.py --ruta <repo-del-reto>/dataset/textos
    uv run scripts/corpus.py --ruta ... --revisar     # informa, no escribe

Los PDFs **no se redistribuyen en este repositorio**: son obra de sus autores y
el material del reto los incluye solo como referencia. Este script los lee de
donde estén y produce el índice, que es lo que se entrega.

Tres cosas que el corpus real trae y que la ingesta resuelve a la vista, no en
silencio:

1. **PDFs sin capa de texto.** El lector no falla con un escaneo: devuelve cadena
   vacía. Indexarlo así mete un documento fantasma que nunca se puede citar y que
   infla el conteo de la consola. Se detecta y se reporta.
2. **Casi-duplicados.** El mismo artículo guardado dos veces con distinto nombre.
   Con huella exacta no se ven —difieren en saltos de línea y encabezados— pero
   con solapamiento de n-gramas sí. Importan porque compiten entre ellos en el
   top-k y desplazan material distinto.
3. **Carpetas cuyo contenido no corresponde a su nombre.** Se etiquetan por lo
   que de verdad tratan (ver `server/knowledge/procedimientos.py`).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.knowledge.chunker import parse_file  # noqa: E402
from server.knowledge.procedimientos import (  # noqa: E402
    PROCEDIMIENTOS,
    TEMA_REAL_DE_CARPETA,
    etiqueta_de_carpeta,
)
from server.knowledge.service import KnowledgeService  # noqa: E402

# Un PDF con menos texto que esto no tiene capa aprovechable: es un escaneo. El
# umbral es generoso para no descartar un documento legítimo y corto.
MIN_CARACTERES = 400

# Dos documentos que comparten esta fracción de sus n-gramas son el mismo
# artículo. Medido sobre este corpus: el único par repetido da 0.96 y el
# siguiente par más parecido no llega a 0.2, así que 0.5 separa sin ambigüedad.
UMBRAL_DUPLICADO = 0.5


def ngramas(texto: str, n: int = 8, paso: int = 3) -> set[tuple[str, ...]]:
    """Huella por solapamiento de n-gramas, tolerante al formato."""
    palabras = re.sub(r"\W+", " ", texto.lower()).split()
    return {tuple(palabras[i:i + n]) for i in range(0, max(0, len(palabras) - n), paso)}


def parecido(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def main() -> int:
    # `line_buffering`: la ingesta tarda minutos y sin esto Python bufferiza la
    # salida cuando no escribe a una terminal. El proceso parece colgado durante
    # toda su ejecución y luego escupe todo de golpe.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    ap = argparse.ArgumentParser(description="Construye el índice del corpus clínico.")
    ap.add_argument("--ruta", required=True, help="carpeta dataset/textos del repo del reto")
    ap.add_argument("--db", default="corpus_reto.db", help="base de conocimiento destino")
    ap.add_argument("--revisar", action="store_true", help="informa qué pasaría; no escribe")
    args = ap.parse_args()

    raiz = Path(args.ruta)
    if not raiz.is_dir():
        print(f"[X] No existe la carpeta: {raiz}")
        return 1

    svc = None if args.revisar else KnowledgeService(db_path=args.db)

    total = ingeridos = 0
    sin_texto: list[str] = []
    duplicados: list[tuple[str, str, float]] = []
    vistos: list[tuple[str, set]] = []
    por_etiqueta: dict[str, int] = {}

    for proc in PROCEDIMIENTOS:
        carpeta = raiz / proc.carpeta
        if not carpeta.is_dir():
            print(f"  [!] falta la carpeta {proc.carpeta!r} ({proc.nombre})")
            continue

        etiqueta = etiqueta_de_carpeta(proc.carpeta) or proc.slug
        aviso = ""
        if proc.carpeta in TEMA_REAL_DE_CARPETA:
            aviso = (f"\n  [!] El contenido de esta carpeta NO es de {proc.nombre}: se indexa"
                     f"\n      como {etiqueta!r}, así que ningún paciente de {proc.nombre} lo"
                     f"\n      recuperará. El agente declarará el límite y escalará.")
        print(f"\n== {proc.nombre}  ({proc.carpeta}/)  ->  etiqueta {etiqueta!r} =={aviso}")

        for pdf in sorted(carpeta.glob("*.pdf")):
            total += 1
            try:
                texto = parse_file(str(pdf))
            except Exception as exc:  # noqa: BLE001 — se reporta y se sigue
                sin_texto.append(f"{pdf.name}  [{type(exc).__name__}: {exc}]")
                print(f"  [X] {pdf.name[:62]}  -> no se pudo leer")
                continue

            if len(texto.strip()) < MIN_CARACTERES:
                sin_texto.append(pdf.name)
                print(f"  [X] {pdf.name[:62]}  -> sin capa de texto ({len(texto.strip())} car.)")
                continue

            firma = ngramas(texto)
            gemelo = next(((n, j) for n, otra in vistos
                           if (j := parecido(firma, otra)) >= UMBRAL_DUPLICADO), None)
            if gemelo:
                duplicados.append((pdf.name, gemelo[0], gemelo[1]))
                print(f"  [=] {pdf.name[:58]}  -> repetido ({gemelo[1]:.2f}) de {gemelo[0][:32]}")
                continue
            vistos.append((pdf.name, firma))

            if svc is not None:
                svc.add_file(str(pdf), procedure=etiqueta)
            ingeridos += 1
            por_etiqueta[etiqueta] = por_etiqueta.get(etiqueta, 0) + 1
            print(f"  ok  {pdf.name[:62]}  ({len(texto):,} car.)")

    print(f"\n{'='*72}")
    print(f"  {total} PDFs revisados · {ingeridos} ingeridos"
          f"{' (simulación)' if args.revisar else ''}")
    for proc in PROCEDIMIENTOS:
        n = por_etiqueta.get(proc.slug, 0)
        marca = "  <-- SIN CORPUS PROPIO: el agente declarará el límite" if n == 0 else ""
        print(f"    {proc.nombre:<32} {n:>3} documentos{marca}")
    for carpeta, tema in TEMA_REAL_DE_CARPETA.items():
        if n := por_etiqueta.get(tema, 0):
            print(f"    {'(' + tema + ')':<32} {n:>3} documentos  <-- venían en {carpeta}/")

    if sin_texto:
        print(f"\n  {len(sin_texto)} sin capa de texto (NO indexados, requieren OCR):")
        for n in sin_texto:
            print(f"    - {n[:100]}")
    if duplicados:
        print(f"\n  {len(duplicados)} casi-duplicados descartados:")
        for a, b, j in duplicados:
            print(f"    - {a[:66]}\n      = {b[:66]}  (Jaccard {j:.2f})")

    if svc is not None:
        svc.close()
        print(f"\n  Índice escrito en {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
