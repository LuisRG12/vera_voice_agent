"""Sesión de llamada por WebSocket: turnos por frases e interrupción.

**Dónde ocurre cada mitad de la voz.**

- **La síntesis, en el servidor y en local** (Piper, `voz/tts.py`). Nada de lo que
  dice el agente sale de la máquina, y suena igual en cualquier equipo.
- **El reconocimiento, en el navegador.** Hoy eso significa que el audio del
  paciente pasa por el servicio de reconocimiento del navegador, que **no es
  local**. Está declarado como limitación conocida en el informe, con lo que
  haría falta para cerrarlo (un reconocedor local, ~37 MB).

El servidor recibe texto y devuelve **frases con su audio**, no la respuesta
completa. Cada frase se envía en cuanto está cerrada, así que el paciente empieza
a oír mientras el modelo todavía genera; esperar al turno completo añadiría
segundos de silencio.

El transcrito de cada frase va **antes** que su audio: la pantalla no tiene por
qué esperar a la síntesis.

**Interrupción (barge-in).** Si el paciente habla mientras el agente responde, se
cancela la generación en curso y se atiende lo nuevo. Sin esto, interrumpir a un
agente que se equivocó exige esperar a que termine — que es exactamente lo que
hace insoportable hablar con una máquina.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from server.governance.limits import MENSAJE_LIMITE


class SesionLlamada:
    """Une el WebSocket con el gestor de diálogo y el registro."""

    def __init__(self, ws: WebSocket, app_state) -> None:
        self.ws = ws
        self.st = app_state
        self.generando: asyncio.Task | None = None

    async def _emitir_turno(self, texto: str) -> None:
        """Genera un turno y va enviando sus frases, con su audio.

        La frase se manda **antes** de sintetizarla: el transcrito aparece de
        inmediato y el audio llega ~300 ms después. Si se esperara a tener el
        audio, la pantalla se quedaría muda ese tiempo sin motivo.
        """
        dm = self.st.dialogo
        try:
            async for tipo, payload in dm.stream_turn(texto):
                if tipo == "speak":
                    await self.ws.send_json({"type": "frase", "text": payload})
                    # La síntesis bloquea, así que va a un hilo. Si no hay voz
                    # local devuelve None y el navegador usa la suya.
                    if wav := await asyncio.to_thread(self.st.tts.sintetizar, payload):
                        await self.ws.send_bytes(wav)
                else:
                    self.st.turno_idx += 1
                    await asyncio.to_thread(self.st.llamadas.record_turn,
                                            self.st.call_id, self.st.turno_idx,
                                            texto, payload)
                    await self.ws.send_json({"type": "turno", **payload.model_dump()})
                    if motivo := payload.governance.get("limite_excedido"):
                        await self.ws.send_json({"type": "limite", "motivo": motivo,
                                                 "text": MENSAJE_LIMITE[motivo]})
        except asyncio.CancelledError:
            # Interrumpido por el paciente: no es un error, es la conducta
            # esperada. Se avisa al cliente para que corte la reproducción.
            await self.ws.send_json({"type": "interrumpido"})
            raise

    async def atender(self) -> None:
        await self.ws.accept()
        await self.ws.send_json({
            "type": "listo",
            "call_id": self.st.call_id,
            "model": self.st.dialogo.llm.model,
            # El cliente necesita saberlo para decidir si usa su propia voz.
            "voz_servidor": self.st.tts.nombre != "navegador",
        })

        try:
            while True:
                msg = json.loads(await self.ws.receive_text())
                tipo = msg.get("type")

                if tipo == "hablar":
                    texto = (msg.get("text") or "").strip()
                    if not texto:
                        continue
                    # Barge-in: lo nuevo manda sobre lo que se estaba diciendo.
                    if self.generando and not self.generando.done():
                        self.generando.cancel()
                        try:
                            await self.generando
                        except (asyncio.CancelledError, Exception):  # noqa: B014
                            pass
                    self.generando = asyncio.create_task(self._emitir_turno(texto))
                    try:
                        await self.generando
                    except asyncio.CancelledError:
                        pass

                elif tipo == "interrumpir":
                    if self.generando and not self.generando.done():
                        self.generando.cancel()

                elif tipo == "colgar":
                    await self.ws.send_json({"type": "adios"})
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if self.generando and not self.generando.done():
                self.generando.cancel()
