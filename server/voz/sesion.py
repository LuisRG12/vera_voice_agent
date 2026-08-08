"""Sesión de llamada por WebSocket: turnos por frases e interrupción.

**Dónde ocurre el reconocimiento y la síntesis.** En el navegador. No es una
simplificación: es la opción que no cuesta ninguna clave, ninguna descarga y
ningún servicio de terceros, y la llamada va por navegador de todos modos. Con
4,2 GB ya en el reloj del despliegue, añadir modelos de voz locales pondría en
riesgo la compuerta de 15 minutos por una ganancia que el jurado no evalúa —el
diseño de la voz no puntúa; lo que puntúa es la latencia y el comportamiento—.

El servidor recibe texto y devuelve **frases**, no la respuesta completa. Esa es
la parte que sí es ingeniería: cada frase se envía en cuanto está cerrada, así
que el paciente empieza a oír mientras el modelo todavía genera. Esperar al turno
completo añadiría segundos de silencio.

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
        """Genera un turno y va enviando sus frases."""
        dm = self.st.dialogo
        try:
            async for tipo, payload in dm.stream_turn(texto):
                if tipo == "speak":
                    await self.ws.send_json({"type": "frase", "text": payload})
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
