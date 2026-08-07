"""Descarga todo lo descargable EN PARALELO y cronometra el resultado.

    uv run scripts/setup.py

Existe por la compuerta de despliegue: la solución tiene que quedar corriendo en
15 minutos siguiendo el README, y lo único que de verdad tarda son dos descargas
grandes e **independientes entre sí**: el modelo del agente y el de embeddings.
En serie son la suma; en paralelo, el mayor de los dos.

Imprime el tiempo real de cada una para poder reportarlo con un número medido y
no estimado.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.config import settings  # noqa: E402

OK, BAD = "  [OK]  ", "  [X]   "


class Tarea(threading.Thread):
    def __init__(self, nombre: str, fn):
        super().__init__(daemon=True)
        self.nombre = nombre
        self._fn = fn
        self.segundos = 0.0
        self.error: str | None = None

    def run(self) -> None:
        t0 = time.perf_counter()
        try:
            self._fn()
        except Exception as exc:  # noqa: BLE001 — se reporta, no se propaga
            self.error = f"{type(exc).__name__}: {exc}"
        self.segundos = time.perf_counter() - t0


def descargar_modelo() -> None:
    exe = shutil.which("ollama")
    if not exe:
        raise RuntimeError(
            "Ollama no está instalado o no está en el PATH. "
            "Instálelo desde https://ollama.com y repita este paso.")
    r = subprocess.run([exe, "pull", settings.llm_model], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200])


def descargar_embeddings() -> None:
    from server.knowledge.embedder import Embedder

    # Una llamada real fuerza la descarga y deja el modelo en caché: así la
    # primera consulta de quien evalúa no paga la espera.
    Embedder().embed_one("prueba de arranque")


def main() -> int:
    # Sin `line_buffering` se ve una pantalla en blanco durante los minutos que
    # tardan las descargas, sin saber si avanza.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    print("\n=== Vera · preparación (descargas en paralelo) ===\n")
    print(f"  modelo del agente : {settings.llm_model}")
    print(f"  embeddings        : {settings.embedding_model}")
    print("\n  Se descargan las dos a la vez. La primera vez tarda varios minutos;")
    print("  después es instantáneo porque quedan en caché.\n")

    tareas = [Tarea("modelo del agente", descargar_modelo),
              Tarea("modelo de embeddings", descargar_embeddings)]
    t0 = time.perf_counter()
    for t in tareas:
        t.start()
    for t in tareas:
        t.join()
    total = time.perf_counter() - t0

    fallos = 0
    for t in tareas:
        if t.error:
            fallos += 1
            print(BAD + f"{t.nombre}: {t.error}")
        else:
            print(OK + f"{t.nombre} listo ({t.segundos:.0f} s)")

    print(f"\n  Total en paralelo: {total:.0f} s "
          f"(en serie habrían sido {sum(t.segundos for t in tareas):.0f} s)")

    if fallos:
        print(f"\nFALTA RESOLVER {fallos} punto(s). Corrija y repita.")
        return 1
    print("\nTodo listo. Arranque con:  uv run main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
