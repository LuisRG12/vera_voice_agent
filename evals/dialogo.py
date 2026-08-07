"""El turno decide bien aunque el modelo se equivoque o no responda.

    uv run python -m evals.dialogo

Se prueba el gestor de diálogo con un modelo **sustituido por un doble**, no con
el real. Eso no es una limitación: es el punto. Lo que se quiere comprobar aquí
es la lógica del turno —cuándo se toma la ruta segura, qué pasa si el modelo se
cae, que las dos rutas se comporten igual—, y con el modelo real esas respuestas
no serían deterministas ni repetibles.

Las dos rutas —texto y voz— se prueban **con los mismos casos**, porque tenerlas
divergentes es la clase de defecto que se corrige en una y sobrevive en la otra.
"""
from __future__ import annotations

import asyncio
import sys

from server.agent.dialogue import DialogueManager
from server.agent.llm import LLMError, StructuredLLM
from server.agent.schemas import GroundedResponse, RiskAssessment
from server.knowledge.service import KnowledgeService

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []

PROTOCOLO = """# Apendicectomía
## Signos de alarma
Consulte de inmediato si presenta fiebre igual o mayor a 38.5 grados, enrojecimiento
creciente de la herida o salida de material purulento.
## Herida
Mantenga la incisión limpia y seca. Puede ducharse a partir del tercer día.
"""


class LLMDoble(StructuredLLM):
    """Doble del modelo: responde siempre lo mismo, o falla siempre."""

    def __init__(self, risk: str = "none", falla: bool = False, cita: int | None = None):
        self.model = "doble"
        self._host = "doble"
        self._url = "doble"
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        self.risk = risk
        self.falla = falla
        self.cita = cita
        self.invocaciones = 0

    def structured(self, system, user, schema, max_tokens=400, retries=1):
        self.invocaciones += 1
        if self.falla:
            raise LLMError("el modelo no responde (simulado)")
        uso = {"input_tokens": 10, "output_tokens": 5}
        if schema is RiskAssessment:
            return RiskAssessment(risk=self.risk, rationale="valoración del doble"), uso
        return GroundedResponse(
            uses_knowledge=True, citation_ids=[self.cita] if self.cita else [],
            extracted_symptoms=[], utterance="Respuesta del doble."), uso

    async def astructured_stream(self, system, user, schema, max_tokens=260):
        self.invocaciones += 1
        if self.falla:
            raise LLMError("el modelo no responde (simulado)")
            yield  # pragma: no cover — lo convierte en generador async
        yield "delta", "Respuesta del doble. "
        yield "final", (GroundedResponse(
            uses_knowledge=True, citation_ids=[self.cita] if self.cita else [],
            extracted_symptoms=[], utterance="Respuesta del doble."),
            {"input_tokens": 10, "output_tokens": 5})


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def con_corpus(llm) -> DialogueManager:
    svc = KnowledgeService(db_path=":memory:")
    svc.add_text("protocolo_apendicectomia.md", PROTOCOLO, procedure="apendicectomia")
    return DialogueManager(svc, llm=llm)


async def por_voz(dm: DialogueManager, texto: str):
    dicho, turno = [], None
    async for kind, payload in dm.stream_turn(texto):
        if kind == "speak":
            dicho.append(payload)
        else:
            turno = payload
    return " ".join(dicho), turno


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== El escalamiento no depende del modelo ==")
    dm = con_corpus(LLMDoble(falla=True))
    t = dm.handle_turn("tengo fiebre de 39 grados y sale pus de la herida")
    check("con el modelo caído, escala igual",
          t.decision.risk == "high" and t.decision.action == "escalate",
          f"{t.decision.risk}/{t.decision.action}")
    check("y la decisión la tomaron las reglas", t.decision.source == "rules", t.decision.source)
    check("el paciente oye qué hacer, no un error",
          "urgencias" in t.utterance and "equipo" in t.utterance, t.utterance)
    check("y queda marcado como degradado",
          t.grounding_flag == "degradado_sin_modelo", t.grounding_flag)

    print("\n== Sin material del procedimiento no se improvisa ==")
    dm = con_corpus(LLMDoble())
    dm.state.procedure = "mastectomia"
    t = dm.handle_turn("¿cuándo me puedo quitar el vendaje del pecho?")
    check("declara que no tiene documentos de esa cirugía",
          t.grounding_flag == "sin_corpus_procedimiento", t.grounding_flag)
    check("sin ninguna cita", t.citations == [], str(t.citations))
    check("y lo dice distinto de «no encontré ese dato»",
          "su cirugía no tengo cargados" in t.utterance, t.utterance)

    print("\n== Un documento general SÍ responde, aunque no tenga procedimiento ==")
    # Es lo que sube el evaluador por la consola: sin etiqueta de cirugía, pero
    # válido para cualquier paciente. La compuerta miraba solo si existía material
    # ETIQUETADO, así que negaba una respuesta que sí estaba.
    svc = KnowledgeService(db_path=":memory:")
    svc.add_text("cuidado_general.md",
                 "# Cuidado de la herida\n## Baño\nPuede ducharse a partir del tercer día, "
                 "secando la incisión suavemente.")
    dm = DialogueManager(svc, llm=LLMDoble(cita=1))
    dm.state.procedure = "mastectomia"  # sin corpus etiquetado propio
    t = dm.handle_turn("¿desde cuándo me puedo duchar?")
    check("no declara ausencia de corpus si el documento general responde",
          t.grounding_flag == "ok", t.grounding_flag)
    check("y lo cita", t.citations and "general" in t.citations[0]["doc_name"],
          str(t.citations))

    print("\n== Una pregunta sin evidencia no se responde de memoria ==")
    dm = con_corpus(LLMDoble())
    dm.state.procedure = "apendicectomia"
    t = dm.handle_turn("¿me puedo hacer un tatuaje la próxima semana?")
    check("toma la ruta segura", t.grounding_flag == "sin_evidencia", t.grounding_flag)
    check("y ofrece avisar al equipo", "equipo clínico" in t.utterance, t.utterance)

    print("\n== Con evidencia sí responde, y cita ==")
    dm = con_corpus(LLMDoble(cita=1))
    dm.state.procedure = "apendicectomia"
    t = dm.handle_turn("¿desde cuándo me puedo duchar?")
    check("responde con el modelo", t.grounding_flag == "ok", t.grounding_flag)
    check("y la cita apunta al protocolo",
          t.citations and "apendicectomia" in t.citations[0]["doc_name"], str(t.citations))

    print("\n== Las dos rutas se comportan igual ==")
    casos = [
        ("mastectomia", "¿cuándo me quitan el vendaje?", "sin_corpus_procedimiento"),
        ("apendicectomia", "¿me puedo hacer un tatuaje?", "sin_evidencia"),
    ]
    for proc, pregunta, marca in casos:
        dm_t = con_corpus(LLMDoble())
        dm_t.state.procedure = proc
        t_texto = dm_t.handle_turn(pregunta)
        dm_v = con_corpus(LLMDoble())
        dm_v.state.procedure = proc
        dicho, t_voz = asyncio.run(por_voz(dm_v, pregunta))
        check(f"[{marca}] texto y voz coinciden",
              t_texto.grounding_flag == t_voz.grounding_flag == marca
              and t_texto.utterance == t_voz.utterance == dicho.strip(),
              f"texto={t_texto.grounding_flag} voz={t_voz.grounding_flag}")

    print("\n== El estado se lleva en código, no en la memoria del modelo ==")
    dm = con_corpus(LLMDoble(cita=1))
    dm.handle_turn("hola, me sacaron el apéndice hace dos días")
    dm.handle_turn("me duele un poco la herida")
    st = dm.state
    check("recuerda el procedimiento", st.procedure == "apendicectomia", str(st.procedure))
    check("y el día postoperatorio", st.day_postop == 2, str(st.day_postop))
    check("y los síntomas ya reportados",
          "dolor" in st.reported_symptoms and "herida" in st.reported_symptoms,
          str(st.reported_symptoms))

    print("\n== El juez corre en paralelo, no en serie ==")
    doble = LLMDoble(cita=1)
    dm = con_corpus(doble)
    dm.state.procedure = "apendicectomia"
    dm.handle_turn("¿desde cuándo me puedo duchar?")
    check("un turno con respuesta invoca al modelo dos veces (respuesta + juez)",
          doble.invocaciones == 2, str(doble.invocaciones))

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
