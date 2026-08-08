"""Estado de la llamada en slots estructurados, no en la memoria del modelo.

Es la pieza que hace fiable a un modelo pequeño: el procedimiento, el día
postoperatorio y los síntomas ya reportados **viven en código**. Al modelo se le
entrega un resumen compacto en cada turno, así que no puede perder el hilo ni
contradecirse entre turnos, porque no es él quien recuerda.

Extraer slots con reglas y no con el modelo tiene además dos ventajas prácticas:
es instantáneo y es comprobable sin gastar una sola invocación.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Procedimientos, en lenguaje de PACIENTE. Nadie dice «me practicaron una
# apendicectomía»: dicen «me sacaron el apéndice». El slug coincide con el de
# `knowledge/procedimientos.py`, que es lo que acota la recuperación.
_PROC = [
    ("colecistectomia", r"colecistectom|ves[ií]cula|c[aá]lculos?\s+biliar"),
    ("apendicectomia", r"apendicectom|apendicit|ap[eé]ndice"),
    ("colectomia", r"colectom|colostom[ií]a|del\s+colon|intestino\s+grueso"),
    ("reemplazo_articular", r"reemplazo\s+(?:total\s+)?de\s+(?:rodilla|cadera)|artroplast|"
                            r"pr[oó]tesis\s+de\s+(?:rodilla|cadera)|"
                            r"me\s+(?:operaron|cambiaron|pusieron).{0,20}(?:rodilla|cadera)"),
    ("mastectomia", r"mastectom|me\s+(?:quitaron|sacaron|operaron)[^.]{0,20}(?:seno|mama|pecho)"),
]

# Menciones que SOLO cuentan si el paciente dice que lo operaron. El nombre de una
# enfermedad no es una cirugía: «cáncer de mama» y «cáncer de colon» estaban entre
# los patrones de arriba y disparaban con «mi mamá tuvo cáncer de mama hace años».
# El agente fijaba mastectomía por los antecedentes familiares de otra persona y,
# como mastectomía no tiene corpus, le respondía a la paciente que no tenía
# documentos de su cirugía. Un procedimiento equivocado acota la recuperación a la
# operación que no es, que es peor que no acotarla.
#
# Van aparte y no dentro del patrón porque el procedimiento lo decide la ÚLTIMA
# mención de la frase: si el patrón arrancara en «me operaron», su posición sería
# la del verbo y no la del órgano, y «me operaron de la vesícula… perdón, del
# colon» se resolvería como vesícula.
_PROC_CON_CIRUGIA = [
    ("colectomia", r"c[aá]ncer\s+de\s+colon"),
    ("mastectomia", r"c[aá]ncer\s+de\s+(?:seno|mama)"),
]
_CIRUGIA_1P = re.compile(
    r"me\s+(?:operaron|oper[eé]|sacaron|quitaron|pusieron|hicieron|cambiaron|intervinieron)"
    r"|mi\s+(?:cirug[ií]a|operaci[oó]n)|cuando\s+me\s+oper")

_TEMAS = {
    "dolor": r"dolor|duele|molest",
    "herida": r"herida|incisi[oó]n|cicatriz|puntos|sutura",
    "fiebre": r"fiebre|calentura|temperatura|(?<!\d)(38|39|40)(?!\d)",
    "sangrado": r"sangr|sangre",
    "medicacion": r"medicament|pastilla|analg[eé]sico|acetaminof[eé]n|tomando|dosis",
}

CHECKLIST = ["dolor", "herida", "fiebre", "medicacion"]

_NUM_PALABRA = {
    "un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15,
}
_DIA = re.compile(r"(?:hace|ya\s+van|van|llevo|llevamos)\s+(\d{1,2})\s+d[ií]as")
_DIA_PALABRA = re.compile(
    rf"(?:hace|ya\s+van|van|llevo)\s+({'|'.join(_NUM_PALABRA)})\s+d[ií]as", re.I)
_DIA_SEMANA = re.compile(rf"(?:hace|van|llevo)\s+(\d|{'|'.join(_NUM_PALABRA)})\s+semanas?", re.I)
_DIA_RELATIVO = [(re.compile(r"\bayer\b", re.I), 1), (re.compile(r"\bantier|anteayer\b", re.I), 2),
                 (re.compile(r"\bhoy\b|\besta\s+ma[nñ]ana\b", re.I), 0)]


@dataclass
class CallState:
    patient_name: str | None = None
    procedure: str | None = None
    day_postop: int | None = None
    phase: str = "apertura"
    covered_topics: set[str] = field(default_factory=set)
    reported_symptoms: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    max_risk: str = "none"
    escalated: bool = False
    turn_count: int = 0

    def summary(self) -> str:
        """Lo que se le pasa al modelo cada turno: poco y estructurado."""
        return (
            f"- Procedimiento: {self.procedure or 'no confirmado'}; "
            f"día postoperatorio: {self.day_postop if self.day_postop is not None else '?'}\n"
            f"- Síntomas ya reportados: {', '.join(self.reported_symptoms) or 'ninguno aún'}\n"
            f"- Temas ya cubiertos: {', '.join(sorted(self.covered_topics)) or 'ninguno'}"
        )

    def snapshot(self) -> dict:
        return {
            "procedure": self.procedure,
            "day_postop": self.day_postop,
            "phase": self.phase,
            "covered_topics": sorted(self.covered_topics),
            "reported_symptoms": list(self.reported_symptoms),
            "max_risk": self.max_risk,
            "escalated": self.escalated,
        }


def _dia_postop(low: str) -> int | None:
    if m := _DIA.search(low):
        return int(m.group(1))
    if m := _DIA_PALABRA.search(low):
        return _NUM_PALABRA[m.group(1).lower()]
    if m := _DIA_SEMANA.search(low):
        n = m.group(1).lower()
        return (int(n) if n.isdigit() else _NUM_PALABRA[n]) * 7
    for rx, dias in _DIA_RELATIVO:
        if rx.search(low):
            return dias
    return None


def menciona_tema_clinico(text: str) -> bool:
    """Si la frase toca alguno de los temas del seguimiento.

    Se apoya en `_TEMAS`, el mismo diccionario con el que se llenan los slots: lo
    que cuenta como clínico para el estado cuenta como clínico para decidir si el
    turno necesita evidencia. Tener una sola lista evita que un síntoma añadido
    aquí se quede fuera allá.
    """
    low = text.lower()
    return any(re.search(pat, low) for pat in _TEMAS.values())


def update_slots_from_text(state: CallState, text: str) -> None:
    """Actualiza el estado con lo que acaba de decir el paciente.

    Tanto el procedimiento como el día los fija la **última** mención de la
    frase, no la primera. Congelar la primera no dejaba forma de corregir: si el
    paciente se equivocaba —o el reconocedor transcribía mal el primer turno— la
    recuperación quedaba sesgada al protocolo equivocado durante toda la llamada.
    Con la última mención, «no fue de la vesícula sino del colon» resuelve bien.
    """
    low = text.lower()

    menciones = [(m.start(), name) for name, pat in _PROC if (m := re.search(pat, low))]
    if _CIRUGIA_1P.search(low):
        # Entran con la posición del ÓRGANO, para que sigan compitiendo por
        # «la última mención manda».
        menciones += [(m.start(), name) for name, pat in _PROC_CON_CIRUGIA
                      if (m := re.search(pat, low))]
    if menciones:
        state.procedure = max(menciones)[1]

    if (dia := _dia_postop(low)) is not None:
        state.day_postop = dia

    for tema, pat in _TEMAS.items():
        if re.search(pat, low) and tema not in state.reported_symptoms:
            state.reported_symptoms.append(tema)
