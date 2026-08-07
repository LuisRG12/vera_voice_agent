"""¿Se reconoce el procedimiento en lo que de verdad dice un paciente?

    uv run python -m evals.deteccion_procedimiento

Es el eslabón del que cuelga toda la compuerta de pertinencia: si el
procedimiento no se detecta, la recuperación no se acota y el agente vuelve a
poder responder una pregunta de una cirugía con documentos de otra.

Las frases son de paciente colombiano, no de historia clínica. Nadie dice «me
practicaron una apendicectomía»: dice «me sacaron el apéndice». Varias vienen
degradadas como las entrega un reconocedor de voz, sin tildes.
"""
from __future__ import annotations

import sys

from server.agent.state import CallState, update_slots_from_text

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []

POSITIVOS = [
    ("me sacaron el apéndice el lunes", "apendicectomia"),
    ("me operaron de apendicitis hace tres días", "apendicectomia"),
    ("tuve una apendicectomía la semana pasada", "apendicectomia"),
    ("me operaron del apendice", "apendicectomia"),  # sin tilde: viene del reconocedor
    ("me sacaron la vesícula", "colecistectomia"),
    ("me hicieron la colecistectomía el viernes", "colecistectomia"),
    ("tenía cálculos biliares y me operaron", "colecistectomia"),
    ("me operaron de la vesicula hace 4 dias", "colecistectomia"),
    ("me operaron del colon", "colectomia"),
    ("me hicieron una colectomía", "colectomia"),
    ("tengo la bolsa de colostomía desde la cirugía", "colectomia"),
    ("me detectaron cáncer de colon y me operaron", "colectomia"),
    ("me pusieron una rodilla nueva", "reemplazo_articular"),
    ("me hicieron el reemplazo total de cadera", "reemplazo_articular"),
    ("tuve una artroplastia de rodilla", "reemplazo_articular"),
    ("me operaron de la cadera hace dos semanas", "reemplazo_articular"),
    ("tengo una prótesis de rodilla", "reemplazo_articular"),
    # Mastectomía es el que más importa: sin detección no se activa la compuerta
    # y el paciente recibiría material de otra cirugía.
    ("me hicieron una mastectomía", "mastectomia"),
    ("me quitaron el seno derecho", "mastectomia"),
    ("me operaron del seno por un cáncer de mama", "mastectomia"),
    ("me sacaron la mama izquierda", "mastectomia"),
]

# Un falso positivo acota la recuperación a la cirugía equivocada, que es peor
# que no acotarla.
NEGATIVOS = [
    "me duele la rodilla de la otra pierna",
    # Antecedentes familiares: el nombre de una enfermedad no es una cirugía.
    # Este caso fijaba `mastectomia` y, como no tiene corpus, el agente le decía
    # a la paciente que no tenía documentos de SU cirugía.
    "mi mamá tuvo cáncer de mama hace años",
    "mi papá murió de cáncer de colon",
    "el dolor me baja por la cadera hasta el pie",
    "buenos días, ¿con quién hablo?",
    "estoy bien, gracias",
]

CORRECCIONES = [
    ("no fue del apéndice, fue de la vesícula", "colecistectomia"),
    ("me operaron de la vesícula… perdón, del colon", "colectomia"),
]

DIAS = [
    ("me operaron hace 4 días", 4),
    ("ya van tres días de la cirugía", 3),
    ("me operaron hace dos semanas", 14),
    ("me operaron ayer", 1),
]


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def detecta(frase: str) -> str | None:
    st = CallState()
    update_slots_from_text(st, frase)
    return st.procedure


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Se reconoce el procedimiento en habla de paciente ==")
    for frase, esperado in POSITIVOS:
        got = detecta(frase)
        check(f"[{esperado:<20}] {frase[:50]}", got == esperado, f"detectó {got!r}")

    print("\n== No se inventa un procedimiento que nadie mencionó ==")
    for frase in NEGATIVOS:
        got = detecta(frase)
        check(f"[{'ninguno':<20}] {frase[:50]}", got is None, f"detectó {got!r}")

    print("\n== El paciente se corrige y manda la última mención ==")
    for frase, esperado in CORRECCIONES:
        got = detecta(frase)
        check(f"[{esperado:<20}] {frase[:50]}", got == esperado, f"detectó {got!r}")

    print("\n== El día postoperatorio, dicho como se dice ==")
    for frase, esperado in DIAS:
        st = CallState()
        update_slots_from_text(st, frase)
        check(f"[día {esperado:<16}] {frase[:50]}", st.day_postop == esperado,
              f"detectó {st.day_postop!r}")

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
