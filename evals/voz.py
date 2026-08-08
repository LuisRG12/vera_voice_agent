"""La llamada entrega frases mientras genera, y se puede interrumpir.

    uv run python -m evals.voz

Prueba el protocolo de la sesión de voz **sin navegador y sin modelo real**: lo
que se verifica es el comportamiento del canal, que es lo que hace que una
llamada se sienta como una conversación.

- Que las frases salgan **una a una** y no de golpe al final. Esperar al turno
  completo añade segundos de silencio.
- Que interrumpir **corte** de verdad la generación en curso. Sin eso, corregir a
  un agente que se equivocó exige esperar a que termine, que es exactamente lo
  que hace insoportable hablar con una máquina.
- Que el turno quede registrado igual que por la ruta de texto.
"""
from __future__ import annotations

import asyncio
import sys

from evals.dialogo import PROTOCOLO, LLMDoble
from server.agent.dialogue import DialogueManager
from server.knowledge.service import KnowledgeService

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


class LLMLento(LLMDoble):
    """Genera varias frases con pausa entre ellas, como el modelo real."""

    def __init__(self, pausa: float = 0.05, frases: int = 4):
        super().__init__(cita=1)
        self.pausa = pausa
        self.frases = frases
        self.emitidas = 0

    async def astructured_stream(self, system, user, schema, max_tokens=260):
        from server.agent.schemas import GroundedResponse

        for i in range(self.frases):
            await asyncio.sleep(self.pausa)
            self.emitidas += 1
            yield "delta", f"Esta es la frase número {i + 1} de la respuesta. "
        yield "final", (GroundedResponse(
            uses_knowledge=True, citation_ids=[1], extracted_symptoms=[],
            utterance="completa"), {"input_tokens": 10, "output_tokens": 20})


def con_corpus(llm) -> DialogueManager:
    svc = KnowledgeService(db_path=":memory:")
    svc.add_text("protocolo_apendicectomia.md", PROTOCOLO, procedure="apendicectomia")
    dm = DialogueManager(svc, llm=llm)
    dm.state.procedure = "apendicectomia"
    return dm


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Las frases salen una a una, no al final ==")
    dm = con_corpus(LLMLento())
    tiempos, frases = [], []
    t0 = asyncio.get_event_loop().time()
    turno = None
    async for tipo, payload in dm.stream_turn("¿desde cuándo me puedo duchar?"):
        if tipo == "speak":
            frases.append(payload)
            tiempos.append(asyncio.get_event_loop().time() - t0)
        else:
            turno = payload
    check("se emitieron varias frases", len(frases) >= 3, str(len(frases)))
    check("la primera llega antes que la última",
          len(tiempos) >= 2 and tiempos[-1] > tiempos[0], str(tiempos))
    check("y el turno se cierra con su decisión", turno is not None and turno.decision)
    check("con la cita derivada", turno.citations, str(turno.citations))

    print("\n== Un umbral clínico no se parte en dos frases ==")
    # Observado en una llamada real: «fiebre igual o mayor a 38.5 grados» se
    # partía en «…mayor a 38.» y «5 grados», y el sintetizador decía «treinta y
    # ocho punto», pausaba, y seguía con «cinco grados».
    from server.agent.stream_parse import SentenceSplitter

    sp = SentenceSplitter()
    salidas = []
    for trozo in ["Avísele a su equipo porque presenta fiebre igual o mayor a 38",
                  ".5 grados y salida de material purulento. ", "Es urgente."]:
        salidas += sp.push(trozo)
    salidas.append(sp.flush())
    completas = [s for s in salidas if s]
    check("el decimal no corta la frase",
          any("38.5 grados" in s for s in completas), str(completas))
    check("pero el punto final sí la corta", len(completas) >= 2, str(completas))

    # Una frase corta que llega después NO se emite sola: espera por `min_chars`
    # y sale al cerrar el turno. Mandar «Puede ducharse.» como emisión propia
    # produciría un jadeo de tres palabras en el sintetizador.
    sp2 = SentenceSplitter()
    trozos = sp2.push("Mantenga la incisión limpia y seca por favor. Puede ducharse. ")
    check("la frase larga sale de inmediato", len(trozos) == 1, str(trozos))
    check("y la corta espera al cierre", sp2.flush() == "Puede ducharse.", repr(sp2.pending))

    print("\n== Interrumpir corta la generación en curso ==")
    dm = con_corpus(LLMLento(pausa=0.15, frases=8))
    emitidas: list[str] = []

    async def hablar():
        async for tipo, payload in dm.stream_turn("¿desde cuándo me puedo duchar?"):
            if tipo == "speak":
                emitidas.append(payload)

    tarea = asyncio.create_task(hablar())
    await asyncio.sleep(0.4)          # deja salir un par de frases
    parciales = len(emitidas)
    tarea.cancel()
    try:
        await tarea
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.4)          # tiempo de sobra para las que faltaban

    check("alcanzó a decir algo antes de cortarse", parciales >= 1, str(parciales))
    check("y NO siguió generando tras la interrupción",
          len(emitidas) == parciales, f"{parciales} -> {len(emitidas)}")

    print("\n== Texto y voz producen el mismo turno ==")
    dm_texto = con_corpus(LLMDoble(cita=1))
    t_texto = dm_texto.handle_turn("¿desde cuándo me puedo duchar?")
    dm_voz = con_corpus(LLMDoble(cita=1))
    t_voz = None
    async for tipo, payload in dm_voz.stream_turn("¿desde cuándo me puedo duchar?"):
        if tipo == "turn":
            t_voz = payload
    check("mismo riesgo", t_texto.decision.risk == t_voz.decision.risk,
          f"{t_texto.decision.risk} vs {t_voz.decision.risk}")
    check("misma marca de grounding", t_texto.grounding_flag == t_voz.grounding_flag,
          f"{t_texto.grounding_flag} vs {t_voz.grounding_flag}")
    check("mismas citas",
          [c["chunk_id"] for c in t_texto.citations] == [c["chunk_id"] for c in t_voz.citations],
          f"{t_texto.citations} vs {t_voz.citations}")

    print("\n== Un signo de alarma escala también por voz ==")
    dm = con_corpus(LLMDoble(cita=1))
    turno = None
    async for tipo, payload in dm.stream_turn("tengo fiebre de 39 y sale pus de la herida"):
        if tipo == "turn":
            turno = payload
    check("escala", turno.decision.action == "escalate", turno.decision.action)
    check("y la decisión la respaldan las reglas",
          turno.decision.source in ("rules", "both"), turno.decision.source)

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
