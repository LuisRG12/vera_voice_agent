"""Las métricas que el README debe reportar, medidas y no estimadas.

    uv run python -m evals.metricas          # con el hardware disponible
    uv run python -m evals.metricas --cpu    # forzando CPU pura

Requiere el índice del corpus construido. **Invoca al modelo**: es lento a
propósito, porque medir la latencia con un doble no mediría nada.

Se reporta lo que la evaluación va a contrastar contra los logs:

- **Latencia**: desde que el paciente termina de hablar hasta que empieza a sonar
  la respuesta. Es TTFA —hasta la primera frase hablable—, no el turno completo:
  se habla por frases mientras el modelo sigue generando.
- **Consumo**: tokens de entrada y salida por turno, **invocaciones al modelo por
  turno** y **consultas al RAG por llamada**.
- **Costo**: cero de API, porque el modelo corre local. Se extrapola a precios de
  producción para que la cifra sea comparable.

Reportar números que no se sostienen es peor que no reportarlos, así que este
arnés imprime el bloque tal como debe ir al README.
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

import server.config as config_mod
from server.agent.dialogue import DialogueManager
from server.knowledge.service import KnowledgeService

# Una llamada representativa: apertura, preguntas con respuesta, una pregunta
# fuera de corpus y un signo de alarma. No solo los casos fáciles.
LLAMADA = [
    "buenas, me sacaron el apéndice hace dos días",
    "me duele un poco la herida pero es aguantable",
    "¿desde cuándo me puedo bañar?",
    "¿me puedo tomar una cerveza con el remedio?",
    "¿cuándo tengo que volver a control?",
    "tengo fiebre de treinta y nueve y la herida me bota un líquido amarillo",
]

# Precio de referencia para la extrapolación: un endpoint serverless de un modelo
# de esta clase (3B). Es una REFERENCIA declarada, no el costo real —que es cero—,
# y sirve para que la cifra se pueda comparar con soluciones que sí pagan API.
USD_POR_MTOK_ENTRADA = 0.10
USD_POR_MTOK_SALIDA = 0.10


class ContadorRAG:
    """Envuelve el servicio para contar consultas sin tocar su código."""

    def __init__(self, svc):
        self._svc = svc
        self.consultas = 0

    def query(self, *a, **kw):
        self.consultas += 1
        return self._svc.query(*a, **kw)

    def __getattr__(self, nombre):
        return getattr(self._svc, nombre)


class ContadorLLM:
    """Envuelve el cliente para contar invocaciones por turno."""

    def __init__(self, llm):
        self._llm = llm
        self.invocaciones = 0

    def structured(self, *a, **kw):
        self.invocaciones += 1
        return self._llm.structured(*a, **kw)

    async def astructured_stream(self, *a, **kw):
        self.invocaciones += 1
        async for x in self._llm.astructured_stream(*a, **kw):
            yield x

    def __getattr__(self, nombre):
        return getattr(self._llm, nombre)


async def turno_medido(dm, texto: str) -> dict:
    """(TTFA, total, tokens) de un turno por la ruta de voz, que es la real."""
    t0 = time.perf_counter()
    ttfa = None
    turno = None
    async for tipo, payload in dm.stream_turn(texto):
        if tipo == "speak" and ttfa is None:
            ttfa = (time.perf_counter() - t0) * 1000
        elif tipo == "turn":
            turno = payload
    total = (time.perf_counter() - t0) * 1000
    return {"ttfa": ttfa if ttfa is not None else total, "total": total, "turno": turno}


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    i = min(int(round(p / 100 * (len(orden) - 1))), len(orden) - 1)
    return orden[i]


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    solo_cpu = "--cpu" in sys.argv
    if solo_cpu:
        config_mod.settings.llm_num_gpu = 0

    corpus = Path(__file__).resolve().parents[1] / "corpus_reto.db"
    if not corpus.exists():
        print("[X] Falta corpus_reto.db. Constrúyalo con scripts/corpus.py")
        return 1

    print(f"\nMétricas · {'CPU pura' if solo_cpu else 'hardware disponible'} · "
          f"{len(LLAMADA)} turnos de una llamada representativa\n")

    svc = ContadorRAG(KnowledgeService(db_path=":memory:", plantilla=str(corpus)))
    dm = DialogueManager(svc)
    dm.llm = ContadorLLM(dm.llm)

    # Un turno de calentamiento: la primera generación paga la carga del modelo
    # en memoria y no representa nada de la conversación real.
    print("  calentando…", end=" ", flush=True)
    await turno_medido(dm, "hola")
    print("listo\n")

    dm2 = DialogueManager(svc, llm=dm.llm)
    medidas, invocaciones = [], []
    svc.consultas = 0
    for texto in LLAMADA:
        antes = dm.llm.invocaciones
        m = await turno_medido(dm2, texto)
        invocaciones.append(dm.llm.invocaciones - antes)
        medidas.append(m)
        t = m["turno"]
        print(f"  TTFA {m['ttfa']:>6.0f} ms · total {m['total']:>6.0f} ms · "
              f"{t.usage['input_tokens']:>5}+{t.usage['output_tokens']:<4} tok · "
              f"{t.decision.risk:<9} {texto[:44]}")

    ttfas = [m["ttfa"] for m in medidas]
    entrada = [m["turno"].usage["input_tokens"] for m in medidas]
    salida = [m["turno"].usage["output_tokens"] for m in medidas]
    coste = (sum(entrada) / 1e6 * USD_POR_MTOK_ENTRADA
             + sum(salida) / 1e6 * USD_POR_MTOK_SALIDA)

    print(f"\n{'='*76}\n  BLOQUE PARA EL README\n{'='*76}\n")
    print(f"| Métrica | {'CPU pura' if solo_cpu else 'Hardware disponible'} |")
    print("|---|---|")
    print(f"| **Latencia** (fin de habla → primera frase hablable) | "
          f"**P50 {percentil(ttfas, 50):.0f} ms** · P95 {percentil(ttfas, 95):.0f} ms |")
    print(f"| Turno completo | P50 {percentil([m['total'] for m in medidas], 50):.0f} ms |")
    print(f"| Tokens por turno | {statistics.median(entrada):.0f} entrada / "
          f"{statistics.median(salida):.0f} salida |")
    print(f"| Tokens por llamada ({len(LLAMADA)} turnos) | {sum(entrada):,} entrada / "
          f"{sum(salida):,} salida |")
    print(f"| **Invocaciones al modelo por turno** | {statistics.median(invocaciones):.0f} "
          f"(respuesta + juez de riesgo, en paralelo) |")
    print(f"| **Consultas al RAG por llamada** | {svc.consultas} |")
    print("| **Costo de API por llamada** | **$0** (el modelo corre local) |")
    print(f"| Costo equivalente si se pagara API | ${coste:.5f} "
          f"(a ${USD_POR_MTOK_ENTRADA}/MTok, referencia declarada) |")
    print("\n  El costo real de API es cero. La extrapolación usa el precio de un")
    print("  endpoint serverless de un modelo de esta clase, declarado arriba, para")
    print("  que la cifra sea comparable con soluciones que sí pagan por token.")

    svc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
