"""Decisión de seguridad: dos capas independientes, se queda la más conservadora.

- **Capa A — determinista** (`safety_rules.py`): léxico clínico colombiano con
  manejo de negación. No invoca al modelo.
- **Capa B — juez** (`assess_risk`): el modelo clasifica el riesgo usando los
  fragmentos del protocolo recuperados.

**Por qué dos y no una.** La capa A no entiende lo que no está en su léxico; la
capa B entiende, pero es un modelo pequeño y puede fallar o no responder. Que la
decisión sea el máximo de ambas significa que **el escalamiento no depende de que
el modelo acierte, ni de que esté disponible**: si el runtime se cae a mitad de
turno, la capa A ya evaluó y la alerta sale igual.

La asimetría es deliberada. En seguridad clínica un falso positivo cuesta una
alerta de más; un falso negativo, un paciente que no fue a urgencias.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from server.agent.llm import StructuredLLM
from server.agent.safety_rules import ACTION_FOR, RuleFlag, max_sev, max_severity
from server.agent.schemas import ActionType, RiskAssessment, RiskLevel

JUEZ_SYSTEM = """Evalúas el riesgo clínico de lo que reporta un paciente recién operado.

- critical: emergencia inmediata (no puede respirar, dolor en el pecho, sangrado masivo, convulsión, desmayo).
- high: signo de alarma que requiere contacto clínico prioritario (fiebre igual o mayor a 38.5, sangrado abundante, pus o signos de infección, signos de trombosis).
- moderate: síntoma que amerita vigilancia y consejo; escalar si empeora.
- low: molestia esperable del postoperatorio.
- none: sin síntoma de riesgo.

Usa los umbrales del PROTOCOLO cuando lo entregado los tenga. Ante duda, SUBE el nivel; nunca lo bajes."""


class SafetyDecision(BaseModel):
    """Decisión combinada, con su justificación persistible."""

    risk: RiskLevel
    action: ActionType
    rationale: str
    rule_flags: list[str] = Field(default_factory=list)
    source: str  # rules | llm | both | none


def formatear_fragmentos(cites) -> str:
    if not cites:
        return "(sin fragmentos relevantes en la base de conocimiento)"
    return "\n".join(f"[#{c.chunk_id} | {c.doc_name} §{c.section}] {c.text}" for c in cites)


def assess_risk(llm: StructuredLLM, user_text: str, cites) -> tuple[RiskAssessment, dict]:
    """Capa B. Devuelve (valoración, consumo de tokens)."""
    user = (f"PROTOCOLO:\n{formatear_fragmentos(cites)}\n\n"
            f"PACIENTE: {user_text}\n\nClasifica el riesgo.")
    # Con margen: el esquema ya acota la longitud de los campos, pero un
    # `rationale` legítimamente largo no debe truncar el JSON y tumbar el turno.
    return llm.structured(JUEZ_SYSTEM, user, RiskAssessment, max_tokens=400)


def combinar(flags: list[RuleFlag], ra: RiskAssessment | None) -> SafetyDecision:
    """El máximo de las dos capas, con constancia de cuál lo decidió."""
    sev_reglas = max_severity(flags)
    sev_juez = ra.risk if ra else "none"
    final = max_sev(sev_reglas, sev_juez)

    if sev_reglas == sev_juez == "none":
        origen = "none"
    elif sev_reglas == final and sev_juez == final:
        origen = "both"
    elif sev_reglas == final:
        origen = "rules"
    else:
        origen = "llm"

    motivos = []
    if flags:
        motivos.append("reglas: " + ", ".join(sorted({f.name for f in flags})))
    if ra and ra.rationale:
        motivos.append(f"juez: {ra.rationale}")

    return SafetyDecision(
        risk=final,
        action=ACTION_FOR[final],
        rationale=" · ".join(motivos) or "Sin señales de riesgo.",
        rule_flags=sorted({f.name for f in flags}),
        source=origen,
    )
