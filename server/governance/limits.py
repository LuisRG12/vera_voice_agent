"""Límites operativos: presupuesto por llamada e interruptor de parada.

Son controles de **producto**, no de laboratorio. Un agente clínico que puede
hablar indefinidamente es un agente que un día se queda en bucle con un paciente
confundido; y uno que no se puede detener es uno que, si empieza a decir algo
mal, sigue diciéndolo hasta que alguien apague el servidor.

El presupuesto no solo se anuncia: se **aplica**. Anunciar un límite y seguir
aceptando turnos es peor que no tenerlo, porque da una falsa sensación de
control.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# Lo que se le dice al paciente cuando la llamada se corta por límite. Nunca se
# le dice «se acabó el presupuesto»: se cierra con cortesía y con la promesa
# —cumplida— de que alguien lo va a contactar.
MENSAJE_LIMITE = {
    "turnos": "Hemos conversado bastante y prefiero que su equipo clínico continúe desde "
              "aquí. Ya les estoy pasando el reporte de todo lo que me contó.",
    "duracion": "Se nos acabó el tiempo de esta llamada. Ya le paso el reporte a su equipo "
                "clínico para que lo contacten.",
}


@dataclass
class CallBudget:
    """Techo de turnos y duración por llamada.

    **El cronómetro arranca en el primer turno, no al construirse.** La
    diferencia parece de detalle y no lo es: la llamada se abre cuando arranca el
    servidor, y quien evalúa levanta el servidor, revisa la consola y sube un
    documento antes de decir la primera palabra. Con el reloj corriendo desde el
    arranque, esa primera llamada llegaba muerta —«se nos acabó el tiempo» en el
    turno uno—, y el límite se aplicaba a una conversación que nunca ocurrió.

    Un presupuesto *por llamada* mide la llamada. Mientras nadie ha hablado, no
    hay llamada que medir.
    """

    max_turnos: int = 25
    max_segundos: float = 15 * 60
    turnos: int = 0
    inicio: float | None = None

    def registrar_turno(self) -> None:
        if self.inicio is None:
            self.inicio = time.monotonic()
        self.turnos += 1

    def transcurrido(self) -> float:
        """Segundos de conversación. Cero mientras nadie haya hablado."""
        return 0.0 if self.inicio is None else time.monotonic() - self.inicio

    def excedido(self) -> str | None:
        """Qué límite se pasó, o None. El nombre sirve de clave del mensaje."""
        if self.turnos >= self.max_turnos:
            return "turnos"
        if self.transcurrido() >= self.max_segundos:
            return "duracion"
        return None

    def snapshot(self) -> dict:
        return {
            "turnos": self.turnos,
            "max_turnos": self.max_turnos,
            "segundos": round(self.transcurrido(), 1),
            "max_segundos": self.max_segundos,
        }


class KillSwitch:
    """Detiene la aceptación de llamadas nuevas, sin reiniciar el servidor.

    Las llamadas en curso NO se cortan a mitad de frase: dejar a un paciente con
    la palabra en la boca sería peor que el motivo por el que se activó. Lo que
    se impide es abrir nuevas.
    """

    def __init__(self) -> None:
        self.activo = False
        self.motivo = ""
        self.desde: float | None = None

    def activar(self, motivo: str) -> None:
        self.activo, self.motivo, self.desde = True, motivo, time.time()

    def liberar(self) -> None:
        self.activo, self.motivo, self.desde = False, "", None

    def snapshot(self) -> dict:
        return {"activo": self.activo, "motivo": self.motivo, "desde": self.desde}
