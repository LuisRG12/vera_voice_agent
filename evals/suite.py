"""Corre todos los arneses deterministas y CUENTA las comprobaciones.

    uv run python -m evals.suite

Existe para que verificar el sistema sea **un comando y no nueve**. Quien evalúe
esto tiene tiempo limitado; obligarlo a recorrer una lista es fricción que no
aporta nada.

Cuenta las comprobaciones leyendo el resumen de cada arnés, no sumándolas a mano:
un número escrito a mano en un README se queda viejo el día que alguien añade un
caso, y un número que se queda viejo es peor que no tenerlo.

Ninguno de estos arneses invoca al modelo de lenguaje: corren en segundos y dan
siempre lo mismo. Los que sí lo invocan —la comparativa que eligió el modelo—
se corren aparte y a conciencia.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time

ARNESES = [
    ("evals.conocimiento_vivo", "ingesta, versionado y olvido en caliente"),
    ("evals.pertinencia_procedimiento", "la recuperación no cruza cirugías"),
    ("evals.alarmas_base", "signos de alarma, casos base"),
    ("evals.alarmas_adversariales", "casos diseñados para romper las reglas"),
    ("evals.lexico_colombiano", "léxico clínico colombiano y sus trampas"),
    ("evals.decision_seguridad", "la decisión sobrevive al modelo caído"),
    ("evals.deteccion_procedimiento", "el procedimiento, en habla de paciente"),
    ("evals.dialogo", "lógica del turno; texto y voz se comportan igual"),
    ("evals.trazabilidad", "citas derivadas y auditoría de cifras"),
    ("evals.gobernanza", "alertas, acuse, límites y registro auditable"),
    ("evals.voz", "frases una a una, interrupción y paridad con texto"),
]

# Cada arnés cierra con su propio resumen. Se aceptan las formas que hay en el
# repositorio, exigiendo inicio de línea para no contar los «[OK]» de cada caso.
_RESUMEN = re.compile(r"^(?:RESULTADO|Resultado|Falsos)[^:]*:\s*(\d+)\s*/\s*(\d+)", re.M)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    print(f"\nCorriendo {len(ARNESES)} arneses (ninguno invoca al modelo)…\n")

    t0 = time.perf_counter()
    total_ok = total = 0
    fallidos: list[tuple[str, str]] = []

    for modulo, que_prueba in ARNESES:
        proc = subprocess.run([sys.executable, "-m", modulo],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        salida = (proc.stdout or "") + (proc.stderr or "")
        marcas = _RESUMEN.findall(salida)
        ok = sum(int(a) for a, _ in marcas)
        n = sum(int(b) for _, b in marcas)
        total_ok += ok
        total += n
        estado = "ok  " if proc.returncode == 0 else "FALLA"
        print(f"  {estado} {ok:>3}/{n:<4} {modulo:<34} {que_prueba}")
        if proc.returncode != 0:
            fallidos.append((modulo, salida))

    segundos = time.perf_counter() - t0
    print(f"\n{total_ok}/{total} comprobaciones en {len(ARNESES)} arneses "
          f"({segundos:.0f} s, sin invocar al modelo).")

    if fallidos:
        print("\nArneses con problema:")
        for modulo, _ in fallidos:
            print(f"  - {modulo}")
        print("Corra el arnés suelto para ver el detalle.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
