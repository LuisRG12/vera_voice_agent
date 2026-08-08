"""Resumen estructurado del cierre de llamada, armado desde los datos.

**No lo redacta el modelo.** Se construye desde el estado de la llamada y los
turnos registrados, así que no puede inventar un síntoma que nadie reportó ni
una decisión que no se tomó. Es el mismo principio del resto del sistema: el
código produce el dato duro, y si el modelo interviene es para redactarlo, nunca
para decidirlo.

Contiene lo que hay que poder responder después de colgar: a quién se llamó y por
qué cirugía, qué reportó, qué se decidió, en qué se apoyó y qué sigue.
"""
from __future__ import annotations


def construir(state, turnos: list[dict]) -> dict:
    """Resumen de la llamada a partir del estado y los turnos registrados."""
    riesgos = [t.get("risk") for t in turnos if t.get("risk")]
    orden = ["none", "low", "moderate", "high", "critical"]
    maximo = max(riesgos, key=orden.index) if riesgos else "none"

    citados = []
    vistos = set()
    for t in turnos:
        for c in t.get("citations", []):
            clave = (c.get("doc_name"), c.get("section"))
            if clave not in vistos:
                vistos.add(clave)
                citados.append({"documento": c.get("doc_name"), "seccion": c.get("section")})

    escalado = maximo in ("high", "critical")
    marcas = sorted({t["grounding_flag"] for t in turnos
                     if t.get("grounding_flag", "ok") != "ok"})

    return {
        "paciente": {
            "procedimiento": state.procedure,
            "dia_postoperatorio": state.day_postop,
        },
        "sintomas_reportados": list(state.reported_symptoms),
        "signos_de_alarma": list(state.red_flags),
        "riesgo_maximo": maximo,
        "escalado": escalado,
        "turnos": len(turnos),
        "referencias_usadas": citados,
        # Lo que la auditoría marcó durante la llamada. Va en el resumen y no
        # solo en el log: quien revise el caso tiene que verlo sin buscarlo.
        "observaciones_de_grounding": marcas,
        "proximos_pasos": (
            ["Contactar al paciente: se detectó un signo de alarma durante la llamada."]
            if escalado else
            ["Seguimiento según el plan habitual; no se detectaron signos de alarma."]
        ),
    }
