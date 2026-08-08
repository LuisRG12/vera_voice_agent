"""Síntesis de voz local, con Piper.

**Por qué no la del navegador.** `speechSynthesis` usa las voces del sistema
operativo, así que el agente suena distinto en cada máquina: en un Windows sin
voz española instalada, Vera habla con acento inglés. Eso no se puede reportar ni
demostrar de forma consistente, y no lo controlamos.

**Por qué local y no un servicio.** Un agente clínico que manda audio del paciente
a un tercero es una decisión que hay que justificar, no una que se toma por
comodidad. Piper corre en la máquina: nada de lo que dice el agente sale de ahí.
Y cuesta 63 MB, contra los gigabytes que ya pesan el modelo de lenguaje y los
embeddings.

**Medido** con `es_MX-claude-high` en esta máquina: carga 1,2 s una sola vez, y
sintetiza a **6,7× tiempo real** —una frase de 3 s tarda ~450 ms—. Como el
diálogo ya entrega frase a frase, cada una se sintetiza mientras suena la
anterior; solo la primera se paga en latencia.

La voz es español **mexicano** y no de España: el neutro latinoamericano le suena
natural a un paciente colombiano, y el peninsular no.

Si el modelo de voz no está, la síntesis devuelve `None` y el navegador cae a su
propia voz. Preferimos que suene peor a que no suene.
"""
from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Protocol

from server.config import settings

VOCES = Path(__file__).resolve().parents[2] / "voces"


class SintetizadorVoz(Protocol):
    """Contrato de un proveedor de voz. Existe para poder cambiarlo —o medir dos
    en paralelo— sin tocar la sesión de llamada."""

    nombre: str

    def sintetizar(self, texto: str) -> bytes | None:
        """WAV completo de una frase, o None si no se puede sintetizar."""
        ...


class PiperTTS:
    """Voz local. El modelo se carga la primera vez que se usa."""

    def __init__(self, modelo: str | None = None):
        self.modelo = VOCES / (modelo or settings.voz_modelo)
        self.nombre = f"piper:{self.modelo.stem}"
        self._voz = None

    @property
    def disponible(self) -> bool:
        return self.modelo.exists()

    def _cargar(self):
        if self._voz is None:
            from piper import PiperVoice

            self._voz = PiperVoice.load(str(self.modelo))
        return self._voz

    def sintetizar(self, texto: str) -> bytes | None:
        if not texto.strip() or not self.disponible:
            return None
        try:
            voz = self._cargar()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                voz.synthesize_wav(texto, w)
            return buf.getvalue()
        except Exception as exc:  # noqa: BLE001 — la llamada sigue sin audio
            print(f"[voz] no se pudo sintetizar: {type(exc).__name__}: {exc}", flush=True)
            return None


class SinVoz:
    """Sin síntesis en el servidor: el navegador usa la suya."""

    nombre = "navegador"
    disponible = True

    def sintetizar(self, texto: str) -> bytes | None:  # noqa: ARG002
        return None


def crear_tts() -> SintetizadorVoz:
    """El sintetizador configurado, o el respaldo si su modelo no está.

    No falla si falta la voz: un agente que no arranca es peor que uno que suena
    con la voz del navegador. El estado real se reporta en `/api/health`.
    """
    if settings.voz_proveedor == "piper":
        piper = PiperTTS()
        if piper.disponible:
            return piper
        print(f"[voz] falta {piper.modelo.name}: se usa la voz del navegador. "
              f"Descárguela con:  uv run scripts/setup.py", flush=True)
    return SinVoz()
