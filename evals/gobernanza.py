"""Alertas con acuse, límites que se aplican y registro auditable.

    uv run python -m evals.gobernanza

Lo que se prueba aquí es que las promesas operativas del sistema **se cumplen**,
no que estén escritas:

- Una alerta no se «resuelve» borrándola: se acusa recibo y queda quién y cuándo.
- Un límite anunciado y no aplicado es peor que no tenerlo.
- El registro permite reconstruir por qué el agente dijo lo que dijo.

No invoca al modelo: el diálogo usa un doble.
"""
from __future__ import annotations

import sys
import tempfile

from evals.dialogo import PROTOCOLO, LLMDoble
from server.agent.dialogue import DialogueManager
from server.governance.limits import CallBudget, KillSwitch
from server.governance.store import GovernanceStore
from server.knowledge.service import KnowledgeService
from server.recorder.resumen import construir
from server.recorder.store import CallStore

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def tmp(nombre: str) -> str:
    return tempfile.mktemp(suffix=f"_{nombre}.db")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Un signo de alarma levanta una alerta con su evidencia ==")
    gov = GovernanceStore(tmp("gov"))
    svc = KnowledgeService(db_path=":memory:")
    svc.add_text("protocolo_apendicectomia.md", PROTOCOLO, procedure="apendicectomia")
    dm = DialogueManager(svc, llm=LLMDoble(cita=1), governance=gov, call_id=7)

    t = dm.handle_turn("tengo fiebre de 39 grados y sale pus de la herida")
    check("la alerta se levanta", t.governance.get("alert_id") is not None, str(t.governance))
    alertas = gov.alerts()
    check("y queda registrada", len(alertas) == 1, str(len(alertas)))
    a = alertas[0]
    check("con la severidad de la decisión", a.severity == "high", a.severity)
    check("con lo que dijo el paciente", "fiebre" in a.evidence["dijo_el_paciente"],
          str(a.evidence))
    check("y con las reglas que dispararon", "fiebre" in a.evidence["reglas"],
          str(a.evidence["reglas"]))
    check("nace activa, sin acusar", a.activa and a.acked_by is None, str(a.acked_by))

    print("\n== Un turno sin riesgo no levanta alerta ==")
    dm2 = DialogueManager(svc, llm=LLMDoble(cita=1), governance=gov, call_id=8)
    t2 = dm2.handle_turn("todo bien, muchas gracias")
    check("no se alerta por conversación normal", "alert_id" not in t2.governance,
          str(t2.governance))

    print("\n== El ciclo se cierra con una persona ==")
    check("un humano acusa recibo", gov.ack(a.id, "Dra. Gómez", "paciente contactado"))
    a2 = gov.alerts()[0]
    check("queda quién lo hizo", a2.acked_by == "Dra. Gómez", str(a2.acked_by))
    check("y su nota", a2.acked_note == "paciente contactado", str(a2.acked_note))
    check("ya no figura como activa", not a2.activa)
    check("no se puede acusar dos veces", gov.ack(a.id, "Otro") is False)
    check("una alerta inexistente no se acusa", gov.ack(999, "Nadie") is False)
    check("el filtro de activas la excluye", gov.alerts(solo_activas=True) == [])

    print("\n== El límite se aplica, no solo se anuncia ==")
    b = CallBudget(max_turnos=3)
    for _ in range(2):
        b.registrar_turno()
    check("dentro del límite no se corta", b.excedido() is None, str(b.excedido()))
    b.registrar_turno()
    check("al alcanzarlo, se declara excedido", b.excedido() == "turnos", str(b.excedido()))
    b2 = CallBudget(max_segundos=0)
    check("también por duración", b2.excedido() == "duracion", str(b2.excedido()))

    print("\n== El interruptor de parada ==")
    ks = KillSwitch()
    check("arranca liberado", not ks.activo)
    ks.activar("revisión clínica")
    check("se activa con su motivo", ks.activo and ks.motivo == "revisión clínica")
    ks.liberar()
    check("y se libera", not ks.activo and ks.motivo == "")

    print("\n== El registro permite reconstruir la llamada ==")
    store = CallStore(tmp("calls"))
    call_id = store.open_call()
    dm3 = DialogueManager(svc, llm=LLMDoble(cita=1), governance=gov, call_id=call_id)
    for i, texto in enumerate(["me sacaron el apéndice hace dos días",
                               "tengo fiebre de 39 y sale pus"], start=1):
        turno = dm3.handle_turn(texto)
        store.record_turn(call_id, i, texto, turno)

    d = store.call_detail(call_id)
    check("guarda los dos turnos", len(d["turns"]) == 2, str(len(d["turns"])))
    ultimo = d["turns"][-1]
    check("con lo que dijo el paciente y lo que respondió el agente",
          "fiebre" in ultimo["patient_text"] and ultimo["agent_text"], str(ultimo)[:120])
    check("con el riesgo y quién lo decidió",
          ultimo["risk"] == "high" and ultimo["source"] in ("rules", "both"),
          f"{ultimo['risk']}/{ultimo['source']}")
    check("con la justificación", "reglas:" in ultimo["rationale"], ultimo["rationale"])
    check("y con la traza de consumo y latencia",
          "input_tokens" in ultimo["usage"] and ultimo["latency_ms"], str(ultimo["usage"]))

    print("\n== El resumen de cierre se arma desde los datos ==")
    resumen = construir(dm3.state, d["turns"])
    store.close_call(call_id, resumen)
    check("identifica el procedimiento", resumen["paciente"]["procedimiento"] == "apendicectomia",
          str(resumen["paciente"]))
    check("y el día postoperatorio", resumen["paciente"]["dia_postoperatorio"] == 2,
          str(resumen["paciente"]))
    check("recoge los signos de alarma", "fiebre" in resumen["signos_de_alarma"],
          str(resumen["signos_de_alarma"]))
    check("marca que se escaló", resumen["escalado"] and resumen["riesgo_maximo"] == "high",
          str(resumen["riesgo_maximo"]))
    check("lista las referencias usadas", resumen["referencias_usadas"],
          str(resumen["referencias_usadas"]))
    check("y deja próximos pasos", resumen["proximos_pasos"], str(resumen["proximos_pasos"]))
    check("la llamada queda cerrada con su resumen",
          store.call_detail(call_id)["summary"] is not None)

    gov.close()
    store.close()
    svc.close()
    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
