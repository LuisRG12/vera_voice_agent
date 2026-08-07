"""La cita se deriva de la evidencia, y al parlante no llega lo que no se habla.

    uv run python -m evals.trazabilidad

Las entradas son respuestas **literales** capturadas del modelo local, no
inventadas para que el arnés pase: se prueba contra lo que el modelo escribe de
verdad.

Dos cosas se comprueban, y la segunda es la que menos se ve venir:

- Que la cita salga aunque el modelo la haya puesto en el sitio equivocado.
- Que las marcas y los restos de JSON **no** lleguen al texto que se sintetiza.
  Sin esto el paciente oye «abre paréntesis citation ids dos» o una llave suelta.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from server.agent.citas import atribuir, derivar, limpiar
from server.agent.grounding import cifras_sin_respaldo

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


@dataclass
class Frag:
    chunk_id: int
    text: str


FRAGMENTOS = [
    Frag(1, "Consulte de inmediato si presenta fiebre igual o mayor a 38.5 grados, "
            "enrojecimiento creciente de la herida o salida de material purulento."),
    Frag(2, "Es normal un dolor leve a moderado los primeros días, que cede con "
            "el analgésico indicado."),
    Frag(3, "Camine con apoyo varias veces al día, en trayectos cortos. Evite "
            "permanecer más de una hora sentado."),
]

# Capturadas del modelo local durante el desarrollo.
REALES = [
    ("Debe avisarse al equipo médico (citation_ids: #1), ya que la salida de "
     "material purulento es un signo de alarma.", [1]),
    ("Su dolor es normalmente leve a moderado según [#2 | plan_casero.md §Dolor], "
     "y cede con el analgésico.", [2]),
    ("En esta semana inicial postoperatoria (#3), se recomienda caminar con apoyo.", [3]),
    ("Su temperatura ha subido, lo cual puede indicar una posible infección "
     "(referencia [#1]).", [1]),
    ("Su herida debe sanar primero; es mejor esperar antes de tomar un baño. "
     "(citation_ids: [#2])", [2]),
    ("Aplique frío según la directiva #3.", [3]),
]


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    permitidos = {f.chunk_id for f in FRAGMENTOS}

    print("\n== Se recupera la cita que el modelo puso en el campo equivocado ==")
    for texto, esperado in REALES:
        _limpio, ids = derivar(texto, FRAGMENTOS, permitidos)
        check(f"cita {esperado} · {texto[:50]}", ids == esperado, f"obtuvo {ids}")

    print("\n== Al sintetizador no llega lo que no se habla ==")
    for texto, _ in REALES:
        limpio, _ids = limpiar(texto)
        limpio_ok = "#" not in limpio and "citation_ids" not in limpio
        check(f"«{limpio[:64]}»", limpio_ok, limpio)

    # Observado en una respuesta real: el modelo dejó escapar un resto del JSON
    # dentro del propio campo de texto. Un sintetizador lo leería en voz alta.
    limpio, _ = limpiar("} Puede ducharse a partir del tercer día.")
    check("una llave suelta del JSON no se habla",
          limpio == "Puede ducharse a partir del tercer día.", repr(limpio))

    print("\n== El texto limpio conserva sentido y puntuación ==")
    limpio, _ = limpiar("Su herida debe sanar primero; es mejor esperar. (citation_ids: [#2])")
    check("no deja espacios ni paréntesis huérfanos",
          limpio.endswith("esperar.") and "  " not in limpio, repr(limpio))

    print("\n== Sin marcas, la cita se deriva del solapamiento ==")
    casos = [
        ("Consulte de inmediato: la salida de material purulento y el "
         "enrojecimiento creciente son signos de alarma.", 1),
        ("Camine con apoyo en trayectos cortos y evite permanecer sentado "
         "más de una hora.", 3),
        ("Ese dolor leve a moderado es normal y cede con el analgésico indicado.", 2),
    ]
    for texto, esperado in casos:
        ids = atribuir(texto, FRAGMENTOS)
        check(f"atribuye a #{esperado} · {texto[:46]}", ids and ids[0] == esperado,
              f"obtuvo {ids}")

    print("\n== No se inventa una cita cuando no la hay ==")
    for texto in ("¿Cómo se ha sentido hoy?",
                  "Voy a avisarle a su equipo clínico.",
                  "Buenos días, le llamo del seguimiento postoperatorio."):
        _limpio, ids = derivar(texto, FRAGMENTOS, permitidos)
        check(f"sin cita · {texto[:50]}", ids == [], f"obtuvo {ids}")

    _l, ids = derivar("Según [#9] debe consultar de inmediato.", FRAGMENTOS, permitidos)
    check("una cita a un fragmento no recuperado se descarta", ids == [], f"obtuvo {ids}")

    _l, ids = derivar("Ese dolor leve a moderado es normal y cede con el analgésico.",
                      FRAGMENTOS, permitidos, declaradas=[2])
    check("lo declarado por el modelo manda sobre lo inferido", ids == [2], f"obtuvo {ids}")

    print("\n== Las cifras se auditan contra la fuente citada ==")
    check("una cifra que está en el fragmento no se marca",
          cifras_sin_respaldo("Consulte si pasa de 38.5 grados", [FRAGMENTOS[0]]) == [],
          str(cifras_sin_respaldo("Consulte si pasa de 38.5 grados", [FRAGMENTOS[0]])))
    check("una cifra inventada sí se marca",
          cifras_sin_respaldo("Deje el vendaje al menos 48 horas", [FRAGMENTOS[0]]) == ["48"],
          str(cifras_sin_respaldo("Deje el vendaje al menos 48 horas", [FRAGMENTOS[0]])))
    check("el eco de lo que dijo el paciente no cuenta como invención",
          cifras_sin_respaldo("¿Cómo se ha sentido estos 4 días?", [FRAGMENTOS[0]],
                              "me operaron hace 4 días") == [], "")
    check("sin fragmentos citados no se audita nada",
          cifras_sin_respaldo("Deje el vendaje 48 horas", []) == [], "")

    # Los pacientes dicen las cifras EN LETRAS. Si el agente las devuelve en
    # dígitos —lo natural al escribir— la auditoría las marcaba como inventadas.
    # Era un falso positivo constante en el caso más frecuente: reportar fiebre.
    for dicho, respuesta, etiqueta in [
        ("tengo treinta y nueve de fiebre", "Tiene fiebre de 39 grados.", "treinta y nueve"),
        ("la fiebre me subió a treinta y ocho y medio", "Tiene 38.5 de temperatura.",
         "treinta y ocho y medio"),
        ("me operaron hace cuatro días", "¿Cómo se ha sentido estos 4 días?", "cuatro"),
    ]:
        check(f"el eco en dígitos de «{etiqueta}» no se marca",
              cifras_sin_respaldo(respuesta, [FRAGMENTOS[0]], dicho) == [],
              str(cifras_sin_respaldo(respuesta, [FRAGMENTOS[0]], dicho)))
    check("pero una cifra que nadie dijo sigue marcándose",
          cifras_sin_respaldo("Espere 48 horas.", [FRAGMENTOS[0]],
                              "tengo treinta y nueve de fiebre") == ["48"], "")

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
