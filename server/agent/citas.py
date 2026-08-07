"""La cita la deriva el código de la evidencia, no la declara el modelo.

Pedirle a un modelo pequeño que rellene `citation_ids` funciona mal. Pero mirando
lo que escribe se ve que **el problema no es que no sepa cuál fragmento usó**:

    «Debe avisarse al equipo médico (citation_ids: #1), ya que la salida…»
    «Su dolor es leve a moderado según [#2 | plan_casero.md §Dolor]…»
    «En esta semana inicial postoperatoria (#3), se recomienda caminar…»

El fragmento correcto está identificado —escrito dentro del texto en vez de en su
campo—. Es un fallo de enrutamiento, no de comprensión.

Eso deja dos trabajos, y el segundo es más urgente de lo que parece:

1. **Recuperar la cita** de donde el modelo la haya puesto y, si no la puso,
   derivarla del solapamiento con la evidencia recuperada.
2. **Sacar del texto lo que no se debe hablar.** Va a un sintetizador de voz: sin
   limpiarlo, el paciente oye «abre paréntesis citation ids dos». Se observó
   además una llave suelta al inicio de una respuesta —resto del JSON que el
   modelo dejó escapar dentro del campo—, que se leería igual en voz alta.

El módulo es determinista y no invoca al modelo: se prueba entero sin gastar un
turno.
"""
from __future__ import annotations

import re

# Marcas de cita que el modelo escribe dentro del texto, en las formas observadas.
_MARCA = re.compile(
    r"""
    \s*
    (?:
        \(\s*(?:citation_ids?|citas?|referencias?|fuentes?)\s*[:=]?\s*[^)]*\)
      | \[\s*\#\d+[^\]]*\]
      | \(\s*\#\d+\s*(?:,\s*\#?\d+\s*)*\)
      | (?:seg[uú]n|referencia|fuente|ver)\s+(?:el\s+)?(?:documento|fragmento)?\s*\#\d+
      | \#\d+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ID = re.compile(r"#\s*(\d+)")

# Restos de estructura JSON que el modelo deja escapar dentro del texto. Se
# quitan solo en los extremos: una llave en mitad de una frase clínica sería
# rarísima, pero quitarla ahí podría alterar una cita textual del protocolo.
_BASURA_EXTREMOS = re.compile(r'^[\s"\'\}\]\{\[,:]+|[\s"\'\{\[]+$')

# Palabras de contenido para la atribución. Se ignoran las funcionales: aparecen
# en cualquier texto clínico y no distinguen un fragmento de otro.
_PALABRA = re.compile(r"[a-záéíóúñü]{5,}")
_VACIAS = {
    "puede", "debe", "tiene", "estar", "hacer", "sobre", "desde", "hasta",
    "cuando", "porque", "aunque", "mientras", "tambien", "también", "para",
    "como", "esta", "este", "estos", "estas", "pueden", "deben", "presenta",
    "presente", "siguiente", "siguientes", "primeros", "primera",
}


def limpiar(texto: str) -> tuple[str, list[int]]:
    """Texto hablable, y los ids que traían las marcas retiradas.

    El texto limpio va al sintetizador; los ids, al registro y al panel de
    trazabilidad, que es donde sirven.
    """
    ids: list[int] = []
    for marca in _MARCA.findall(texto):
        ids.extend(int(n) for n in _ID.findall(marca))
    limpio = _MARCA.sub("", texto)
    limpio = _BASURA_EXTREMOS.sub("", limpio)
    # La marca suele dejar un espacio antes de la puntuación, y un paréntesis
    # huérfano si envolvía solo a la referencia.
    limpio = re.sub(r"\s+([.,;:!?])", r"\1", limpio)
    limpio = re.sub(r"[(\[]\s*[)\]]", "", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip()
    return limpio, list(dict.fromkeys(ids))


def _contenido(texto: str) -> set[str]:
    return {w for w in _PALABRA.findall(texto.lower()) if w not in _VACIAS}


def atribuir(utterance: str, cites, minimo: int = 2) -> list[int]:
    """De qué fragmentos procede lo dicho, por solapamiento de contenido.

    Es la red para cuando el modelo no marcó nada. No adivina intención: mide de
    qué fragmento salen las palabras que el agente usó. Con `minimo=2` hacen
    falta al menos dos palabras de contenido compartidas, que es lo que separa
    «esta frase viene de aquí» de «las dos hablan de medicina».

    Devuelve los ids ordenados por solapamiento, de mayor a menor.
    """
    dichas = _contenido(utterance)
    if not dichas:
        return []
    puntuadas = []
    for c in cites:
        comunes = dichas & _contenido(getattr(c, "text", "") or "")
        if len(comunes) >= minimo:
            puntuadas.append((len(comunes), c.chunk_id))
    puntuadas.sort(reverse=True)
    return [cid for _, cid in puntuadas]


def derivar(utterance: str, cites, permitidos: set[int] | None = None,
            declaradas: list[int] | None = None) -> tuple[str, list[int]]:
    """(texto para hablar, citas verificadas).

    Orden de preferencia, de más fiable a menos:

    1. Lo que el modelo declaró en su campo —cuando acierta, es lo más directo—.
    2. Las marcas que escribió dentro del texto: misma intención, campo errado.
    3. La atribución por solapamiento, cuando no dijo nada pero sí usó la
       evidencia.

    Todo se filtra contra `permitidos`, que son los fragmentos realmente
    recuperados en ESTE turno: una cita a algo que no se le mostró al modelo no
    es una cita, es una invención.
    """
    limpio, en_texto = limpiar(utterance)
    permitidos = permitidos if permitidos is not None else {c.chunk_id for c in cites}

    for candidatas in (declaradas or [], en_texto, atribuir(limpio, cites)):
        validas = [i for i in dict.fromkeys(candidatas) if i in permitidos]
        if validas:
            return limpio, validas
    return limpio, []
