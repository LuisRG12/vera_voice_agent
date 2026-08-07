"""La decisión es el máximo de las dos capas, y sobrevive al modelo caído.

    uv run python -m evals.decision_seguridad

Lo que se prueba aquí no es cada capa por separado —eso lo hacen `alarmas_base`
y `alarmas_adversariales`— sino **la regla de combinación**, que es donde vive la
promesa de seguridad del sistema:

- Si cualquiera de las dos ve riesgo, la decisión lo refleja.
- Si el juez no responde, la decisión sigue saliendo con lo que vieron las
  reglas. Escalar no puede quedar condicionado a que el modelo esté disponible.
- Queda constancia de **cuál capa lo decidió**, porque una decisión clínica que
  no se puede explicar no sirve para auditarla después.

No invoca al modelo: la valoración del juez se pasa como dato.
"""
from __future__ import annotations

import sys

from server.agent.safety_rules import detect_red_flags
from server.agent.schemas import RiskAssessment
from server.agent.seguridad import combinar

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def juez(risk: str, motivo: str = "valoración del modelo") -> RiskAssessment:
    return RiskAssessment(risk=risk, rationale=motivo)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Gana la capa más conservadora ==")
    casos = [
        # (texto, riesgo del juez, riesgo esperado, origen esperado)
        ("tengo fiebre de 39 grados", "none", "high", "rules"),
        ("me duele un poquito la herida", "high", "high", "llm"),
        ("sale pus con mal olor de la herida", "high", "high", "both"),
        ("todo bien, muchas gracias", "none", "none", "none"),
        ("me duele mucho el pecho", "moderate", "critical", "rules"),
    ]
    for texto, r_juez, esperado, origen in casos:
        d = combinar(detect_red_flags(texto), juez(r_juez))
        ok = d.risk == esperado and d.source == origen
        check(f"[{esperado:<8} · {origen:<5}] {texto[:44]}", ok,
              f"obtuvo riesgo={d.risk} origen={d.source}")

    print("\n== El escalamiento no depende de que el modelo responda ==")
    d = combinar(detect_red_flags("tengo fiebre de 39 y sale pus de la herida"), None)
    check("sin juez, las reglas deciden solas", d.risk == "high" and d.source == "rules",
          f"riesgo={d.risk} origen={d.source}")
    check("y la acción es escalar", d.action == "escalate", d.action)
    check("con la evidencia de qué reglas dispararon",
          "fiebre" in d.rule_flags and "infeccion" in d.rule_flags, str(d.rule_flags))

    d = combinar([], None)
    check("sin juez y sin señales, no se inventa riesgo",
          d.risk == "none" and d.action == "continue", f"{d.risk}/{d.action}")

    print("\n== La acción se deriva del riesgo, sin excepciones ==")
    for riesgo, accion in (("none", "continue"), ("low", "continue"), ("moderate", "advise"),
                           ("high", "escalate"), ("critical", "emergency")):
        d = combinar([], juez(riesgo))
        check(f"{riesgo:<9} -> {accion}", d.action == accion, d.action)

    print("\n== La justificación queda escrita, no implícita ==")
    d = combinar(detect_red_flags("me cuesta respirar"), juez("critical", "disnea aguda"))
    check("menciona las reglas que dispararon", "reglas:" in d.rationale, d.rationale)
    check("y el motivo del juez", "disnea aguda" in d.rationale, d.rationale)

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
