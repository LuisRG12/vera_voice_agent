"""Gestor de diálogo: fases clínicas, slots y decisión de seguridad por turno.

Dos rutas de ejecución comparten TODO lo que decide algo:

- `handle_turn` — síncrona, para la ruta de texto.
- `stream_turn` — asíncrona por frases, para la ruta de voz.

**Por qué eso importa tanto que se dice aquí arriba.** La tentación es escribir
cada ruta por su lado, y el resultado es que un control se corrige en una y no en
la otra. Es una clase de defecto especialmente traicionera: la prueba pasa por
donde se corrigió, y el fallo vive en la ruta que nadie volvió a mirar. Por eso
`_preparar`, `_recuperar`, `_ruta_segura` y `_cerrar` son de las dos, y las rutas
solo se diferencian en cómo entregan el texto.
"""
from __future__ import annotations

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor

from server.agent.citas import derivar, limpiar
from server.agent.grounding import cifras_sin_respaldo
from server.agent.llm import LLMError, StructuredLLM
from server.agent.prompts import (
    DEGRADADO,
    DEGRADADO_CON_ALARMA,
    RESPONDER_SYSTEM,
    SIN_CORPUS_PROCEDIMIENTO,
    SIN_INFORMACION,
)
from server.agent.safety_rules import detect_red_flags, max_severity
from server.agent.schemas import AgentTurn, RiskAssessment, grounded_response_for
from server.agent.seguridad import assess_risk, combinar, formatear_fragmentos
from server.agent.state import CHECKLIST, CallState, update_slots_from_text
from server.agent.stream_parse import SentenceSplitter
from server.governance.limits import CallBudget

# Presupuesto de contexto en caracteres. No basta con un `k` fijo de fragmentos:
# el tamaño de un fragmento varía mucho entre un instructivo de paciente y un
# paper, y unos pocos de los grandes desbordan la ventana del modelo. Al
# desbordarse trunca por el principio —donde está el prompt de sistema con las
# reglas de seguridad— y lo hace en silencio.
MAX_CHARS_EVIDENCIA = 7000

_PREGUNTA = re.compile(r"\?|^\s*(?:qu[eé]|c[oó]mo|cu[aá]ndo|cu[aá]nto|d[oó]nde|por\s+qu[eé]|"
                       r"puedo|debo|tengo\s+que|es\s+normal|hay\s+que)\b", re.I)


def es_pregunta(texto: str) -> bool:
    return bool(_PREGUNTA.search(texto.strip()))


def recortar_evidencia(cites, max_chars: int = MAX_CHARS_EVIDENCIA):
    """Los fragmentos que caben en el presupuesto, respetando el ranking.

    Se cortan fragmentos enteros, no se parten: medio fragmento citado como
    fuente es una cita que no resiste verificación contra el documento real.
    """
    elegidos, usados = [], 0
    for c in cites:
        coste = len(c.text) + 80  # el encabezado [#id | doc §sección]
        if elegidos and usados + coste > max_chars:
            break
        elegidos.append(c)
        usados += coste
    return elegidos


class DialogueManager:
    def __init__(self, knowledge, llm: StructuredLLM | None = None,
                 governance=None, call_id: int | None = None):
        self.k = knowledge
        self.llm = llm or StructuredLLM()
        self.state = CallState()
        self.gov = governance
        self.call_id = call_id
        self.budget = CallBudget()

    def _alertar(self, turn: AgentTurn, user_text: str) -> None:
        """Levanta la alerta al equipo clínico cuando la decisión lo amerita.

        Va en `_cerrar` y no en cada ruta: es lo único por lo que pasan las dos.
        La evidencia se guarda junto a la alerta —lo que dijo el paciente, qué
        reglas dispararon, qué la justifica— porque una alerta sin su porqué
        obliga a quien la recibe a reconstruirla a mano.
        """
        if self.gov is None or turn.decision.risk not in ("high", "critical"):
            return
        try:
            alert_id = self.gov.raise_alert(
                self.call_id, turn.decision.risk, turn.decision.rationale,
                {"dijo_el_paciente": user_text,
                 "reglas": turn.decision.rule_flags,
                 "turno": self.state.turn_count,
                 "citas": turn.citations})
            turn.governance = {"alert_id": alert_id}
        except Exception as exc:  # noqa: BLE001 — una alerta fallida no tumba el turno
            turn.governance = {"alert_error": str(exc)}

    # ---------------------------------------------------------------- comunes
    def _preparar(self, user_text: str) -> tuple[list, str]:
        """Actualiza slots y decide el objetivo del turno."""
        st = self.state
        st.turn_count += 1
        update_slots_from_text(st, user_text)

        flags = detect_red_flags(user_text)
        for f in flags:
            if f.name not in st.red_flags:
                st.red_flags.append(f.name)

        if max_severity(flags) in ("high", "critical"):
            objetivo = "encaminar al paciente a contactar a su equipo clínico"
        elif es_pregunta(user_text):
            objetivo = "responder lo que pregunta, solo con el contexto"
        else:
            pendiente = next((t for t in CHECKLIST if t not in st.covered_topics), None)
            if pendiente:
                st.covered_topics.add(pendiente)
            objetivo = (f"preguntar por {pendiente}" if pendiente
                        else "cerrar la llamada con un resumen breve")
        return flags, objetivo

    def _recuperar(self, user_text: str) -> tuple[list, dict]:
        """Recupera evidencia acotada al procedimiento del paciente."""
        proc = self.state.procedure
        q = self.k.query(user_text, k=8, procedimiento=proc)
        # «No tengo documentos de su cirugía» y «no encontré ese dato» son cosas
        # distintas y se dicen distinto. Pero lo primero exige las DOS
        # condiciones: que no haya material etiquetado con ese procedimiento **y**
        # que tampoco haya evidencia entre los documentos generales.
        #
        # Con una sola condición la compuerta se equivocaba en un caso real: un
        # paciente de apendicectomía preguntó cuándo podía ducharse, un documento
        # subido por la consola —sin etiqueta de procedimiento, como todos los
        # que sube el evaluador— lo respondía, y aun así recibió «para su cirugía
        # no tengo cargados documentos». Negarle una respuesta que sí estaba es
        # tan defectuoso como inventarla.
        sin_etiquetados = bool(proc) and proc not in self.k.procedures_present()
        q = {**q, "sin_corpus_procedimiento": sin_etiquetados and not q["has_evidence"]}
        return recortar_evidencia(q["citations"][:5]), q

    @staticmethod
    def _ruta_segura(user_text: str, q: dict) -> tuple[str, str] | None:
        """(qué decir, marca) cuando el turno NO debe generar contenido clínico.

        Compartida por las dos rutas a propósito. Cuando esto vivía solo en la
        ruta de voz, una paciente de mastectomía —procedimiento sin corpus—
        preguntó por texto cuándo quitarse el vendaje y recibió «se recomienda
        dejarlo al menos 48 horas»: una cifra inventada, sin una sola cita.

        Sin material del procedimiento, **cualquier** pregunta clínica va por
        aquí; no hace falta que el turno sea de preguntas abiertas.
        """
        if not es_pregunta(user_text):
            return None
        if q.get("sin_corpus_procedimiento"):
            return SIN_CORPUS_PROCEDIMIENTO, "sin_corpus_procedimiento"
        if not q["has_evidence"]:
            return SIN_INFORMACION, "sin_evidencia"
        return None

    def _prompt(self, user_text: str, cites, objetivo: str) -> str:
        return "\n".join([
            f"ESTADO DE LA LLAMADA:\n{self.state.summary()}\n",
            f"CONTEXTO:\n{formatear_fragmentos(cites)}\n",
            f"OBJETIVO DE ESTE TURNO: {objetivo}\n",
            f"PACIENTE: {user_text}\n",
            "Responde como Vera. Usa SOLO el contexto para afirmaciones clínicas. "
            "No vuelvas a preguntar lo que ya sabes del estado.",
        ])

    def _cerrar(self, flags, ra, utterance: str, cites, citation_ids: list[int],
                usage: dict, lat: dict, marca: str = "ok",
                user_text: str = "") -> AgentTurn:
        """Punto único por el que pasan las dos rutas."""
        st = self.state
        decision = combinar(flags, ra)

        # Auditoría numérica. Va aquí y no en cada ruta porque `_cerrar` es lo
        # único por lo que pasan las dos: ponerlo en `handle_turn` habría dejado
        # la ruta de voz sin ello.
        usados_para_cifras = [c for c in cites if c.chunk_id in set(citation_ids)]
        if marca == "ok" and (huerfanas := cifras_sin_respaldo(
                utterance, usados_para_cifras, user_text)):
            marca = f"cifra_sin_respaldo:{','.join(huerfanas)}"
        st.max_risk = max((st.max_risk, decision.risk),
                          key=["none", "low", "moderate", "high", "critical"].index)
        if decision.risk in ("high", "critical"):
            st.escalated = True
            st.phase = "escalamiento"

        usados = [c for c in cites if c.chunk_id in set(citation_ids)]
        turno = AgentTurn(
            utterance=utterance,
            has_evidence=bool(citation_ids),
            citations=[{"chunk_id": c.chunk_id, "doc_name": c.doc_name, "section": c.section}
                       for c in usados],
            decision=decision,
            phase=st.phase,
            grounding_flag=marca,
            state=st.snapshot(),
            usage=usage,
            latency_ms=lat,
        )
        self.budget.registrar_turno()
        self._alertar(turno, user_text)
        turno.governance.setdefault("presupuesto", self.budget.snapshot())
        if motivo := self.budget.excedido():
            turno.governance["limite_excedido"] = motivo
        return turno

    def _turno_degradado(self, flags, lat: dict, exc: Exception | None) -> AgentTurn:
        """Turno seguro cuando el modelo no responde.

        El escalamiento NO queda condicionado a que el modelo esté disponible: la
        capa determinista ya evaluó. Se decide con reglas, se le dice al paciente
        qué hacer, y el mensaje promete exactamente lo que va a ocurrir.
        """
        if exc is not None:
            # Sin esto, el turno degradaba y en los logs no quedaba ni el tipo de
            # excepción: un fallo intermitente e imposible de reproducir.
            print(f"[degradado] el turno cayó a la ruta segura: "
                  f"{type(exc).__name__}: {str(exc)[:300]}", flush=True)
        urgente = max_severity(flags) in ("high", "critical")
        return self._cerrar(
            flags, None, DEGRADADO_CON_ALARMA if urgente else DEGRADADO,
            [], [], {"input_tokens": 0, "output_tokens": 0}, lat, "degradado_sin_modelo")

    # ------------------------------------------------------------ ruta texto
    def handle_turn(self, user_text: str) -> AgentTurn:
        flags, objetivo = self._preparar(user_text)

        t0 = time.perf_counter()
        cites, q = self._recuperar(user_text)
        lat = {"recuperacion_ms": round((time.perf_counter() - t0) * 1000)}

        t1 = time.perf_counter()
        if (segura := self._ruta_segura(user_text, q)) is not None:
            respuesta, marca = segura
            try:
                ra, uso = assess_risk(self.llm, user_text, cites, flags)
            except LLMError:
                ra, uso = None, {"input_tokens": 0, "output_tokens": 0}
            lat["modelo_ms"] = round((time.perf_counter() - t1) * 1000)
            return self._cerrar(flags, ra, respuesta, cites, [], uso, lat, marca, user_text)

        # Respuesta y juez en paralelo: el juez no depende del texto generado.
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_resp = ex.submit(self._responder, user_text, cites, objetivo)
                f_juez = ex.submit(assess_risk, self.llm, user_text, cites, flags)
                resp, uso_resp = f_resp.result()
                ra, uso_juez = f_juez.result()
        except Exception as exc:  # noqa: BLE001 — se degrada, no se propaga
            lat["modelo_ms"] = round((time.perf_counter() - t1) * 1000)
            return self._turno_degradado(flags, lat, exc)

        lat["modelo_ms"] = round((time.perf_counter() - t1) * 1000)
        uso = {"input_tokens": uso_resp["input_tokens"] + uso_juez["input_tokens"],
               "output_tokens": uso_resp["output_tokens"] + uso_juez["output_tokens"]}
        # La cita la deriva el código; de paso el texto queda limpio de marcas y
        # de restos de JSON que un sintetizador leería en voz alta.
        texto, citas = derivar(resp.utterance, cites, {c.chunk_id for c in cites},
                               declaradas=list(resp.citation_ids))
        return self._cerrar(flags, ra, texto, cites, citas, uso, lat, user_text=user_text)

    def _responder(self, user_text: str, cites, objetivo: str):
        permitidos = tuple(sorted(c.chunk_id for c in cites))
        return self.llm.structured(
            RESPONDER_SYSTEM, self._prompt(user_text, cites, objetivo),
            grounded_response_for(permitidos), max_tokens=260)

    # -------------------------------------------------------------- ruta voz
    async def stream_turn(self, user_text: str):
        """Genera eventos ("speak", frase) y termina con ("turn", AgentTurn).

        Se habla por frases mientras el modelo sigue generando: esperar al JSON
        completo añadiría segundos de silencio en una llamada.
        """
        flags, objetivo = self._preparar(user_text)

        t0 = time.perf_counter()
        cites, q = self._recuperar(user_text)
        lat = {"recuperacion_ms": round((time.perf_counter() - t0) * 1000)}

        juez = asyncio.create_task(
            asyncio.to_thread(assess_risk, self.llm, user_text, cites, flags))
        t1 = time.perf_counter()

        if (segura := self._ruta_segura(user_text, q)) is not None:
            respuesta, marca = segura
            yield "speak", respuesta
            ra, uso = await self._esperar_juez(juez)
            lat["modelo_ms"] = round((time.perf_counter() - t1) * 1000)
            yield "turn", self._cerrar(flags, ra, respuesta, cites, [], uso, lat, marca)
            return

        splitter = SentenceSplitter()
        dichas: list[str] = []
        marcas: list[int] = []
        obj = None
        uso_resp = {"input_tokens": 0, "output_tokens": 0}
        permitidos = tuple(sorted(c.chunk_id for c in cites))
        try:
            async for kind, payload in self.llm.astructured_stream(
                RESPONDER_SYSTEM, self._prompt(user_text, cites, objetivo),
                grounded_response_for(permitidos)
            ):
                if kind == "delta":
                    for frase in splitter.push(payload):
                        # La limpieza va AQUÍ, frase a frase, y no al final: en
                        # voz cada frase se sintetiza en cuanto está completa, así
                        # que limpiar después no llega a tiempo y el paciente
                        # oiría «abre paréntesis citation ids dos».
                        frase, ids = limpiar(frase)
                        marcas.extend(ids)
                        if not frase:
                            continue
                        dichas.append(frase)
                        yield "speak", frase
                elif kind == "final":
                    obj, uso_resp = payload
        except Exception as exc:  # noqa: BLE001 — se degrada, no se propaga
            juez.cancel()
            lat["modelo_ms"] = round((time.perf_counter() - t1) * 1000)
            turno = self._turno_degradado(flags, lat, exc)
            yield "speak", turno.utterance
            yield "turn", turno
            return

        if resto := splitter.flush():
            resto, ids = limpiar(resto)
            marcas.extend(ids)
            if resto:
                dichas.append(resto)
                yield "speak", resto

        utterance = " ".join(dichas)
        if not utterance and obj:
            utterance, ids = limpiar(obj.utterance)
            marcas.extend(ids)
            yield "speak", utterance

        ra, uso_juez = await self._esperar_juez(juez)
        lat["modelo_ms"] = round((time.perf_counter() - t1) * 1000)
        uso = {"input_tokens": uso_resp["input_tokens"] + uso_juez["input_tokens"],
               "output_tokens": uso_resp["output_tokens"] + uso_juez["output_tokens"]}
        # El texto ya se limpió frase a frase; aquí solo se decide la cita, con
        # las marcas recogidas por el camino como segunda fuente.
        _, citas = derivar(utterance, cites, set(permitidos),
                           declaradas=list(obj.citation_ids if obj else []) + marcas)
        yield "turn", self._cerrar(flags, ra, utterance, cites, citas, uso, lat,
                                   user_text=user_text)

    @staticmethod
    async def _esperar_juez(tarea) -> tuple[RiskAssessment | None, dict]:
        """Perder el juez degrada la evaluación, no la anula: la capa
        determinista ya decidió y su veredicto sigue en pie."""
        try:
            return await tarea
        except Exception:  # noqa: BLE001
            return None, {"input_tokens": 0, "output_tokens": 0}
