"""D2 — qué modelo de embeddings separa lo pertinente de lo ajeno.

    uv run python -m evals.spike_embeddings              # muestra del corpus
    uv run python -m evals.spike_embeddings --completo   # corpus entero (lento)

**La métrica correcta no es la similitud media: es la SEPARACIÓN.** Un modelo que
puntúa alto en todo no sirve, porque no permite decidir cuándo abstenerse — y
saber cuándo callar es la mitad del trabajo en un agente clínico.

Se comparan candidatos sobre el MISMO corpus y las MISMAS preguntas etiquetadas
(`evals/calibracion.py`), midiendo:

- **AUC**: probabilidad de que una pregunta respondible puntúe por encima de una
  ajena, tomadas al azar. 1.0 = separación perfecta, 0.5 = azar. No depende de
  dónde se ponga el umbral, que es justo lo que se quiere saber antes de elegirlo.
- **Mejor F1 alcanzable** y el umbral que lo consigue.
- **Aciertos de recuperación**: de nada sirve separar bien si trae el documento
  equivocado.
- **Coste**: descarga, construcción del índice y milisegundos por consulta.

Los modelos de la familia `e5` esperan prefijos `query:` / `passage:` —están
entrenados para recuperación, no para medir parecido—. `Embedder` ya los aplica.
"""
from __future__ import annotations

import sys
import time

import numpy as np

from evals.calibracion import FUERA_DE_CORPUS, RESPONDIBLES
from server.knowledge.retriever import _cosine

CANDIDATOS = [
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # el actual
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
    "intfloat/multilingual-e5-large",
]

# Se conservan los DISTRACTORES —documentos de otros procedimientos— porque el
# problema es precisamente que la recuperación cruza. Una muestra sin ellos
# mediría un problema más fácil que el real.
DOCS_POR_PROC = 6


def auc(positivos: list[float], negativos: list[float]) -> float:
    """Estadístico de Mann-Whitney, exacto a estos tamaños y sin dependencias."""
    if not positivos or not negativos:
        return float("nan")
    ganados = sum((1.0 if p > n else 0.5 if p == n else 0.0)
                  for p in positivos for n in negativos)
    return ganados / (len(positivos) * len(negativos))


def mejor_f1(positivos: list[float], negativos: list[float]) -> tuple[float, float, int, int]:
    """(F1, umbral, rechazos falsos, afirmaciones falsas) en el mejor punto."""
    mejor = (0.0, 0.0, len(positivos), len(negativos))
    for u in sorted({round(v, 3) for v in positivos + negativos}):
        vp = sum(1 for p in positivos if p >= u)
        fp = sum(1 for n in negativos if n >= u)
        fn = len(positivos) - vp
        if vp == 0:
            continue
        f1 = 2 * vp / (2 * vp + fp + fn)
        if f1 > mejor[0]:
            mejor = (f1, u, fn, fp)
    return mejor


def cargar_chunks(completo: bool):
    from pathlib import Path

    from server.knowledge.store import KnowledgeStore

    ruta = Path(__file__).resolve().parents[1] / "corpus_reto.db"
    if not ruta.exists():
        print("[X] Falta corpus_reto.db. Constrúyalo con scripts/corpus.py")
        return None
    chunks = KnowledgeStore(str(ruta)).active_chunks()
    if completo:
        return chunks

    por_proc: dict[str, list[int]] = {}
    elegidos: set[int] = set()
    for c in chunks:
        vistos = por_proc.setdefault(c.procedure or "general", [])
        if c.doc_id not in vistos:
            if len(vistos) >= DOCS_POR_PROC:
                continue
            vistos.append(c.doc_id)
        elegidos.add(c.doc_id)
    return [c for c in chunks if c.doc_id in elegidos]


def evaluar(modelo: str, chunks) -> dict | None:
    from fastembed import TextEmbedding

    print(f"\n  {modelo}")
    t0 = time.perf_counter()
    try:
        emb = TextEmbedding(model_name=modelo)
    except Exception as exc:  # noqa: BLE001 — se reporta y se pasa al siguiente
        print(f"    [X] no disponible: {type(exc).__name__}: {str(exc)[:120]}")
        return None

    mat = np.stack([np.asarray(v, dtype=np.float32)
                    for v in emb.passage_embed([c.text for c in chunks])])
    seg_index = time.perf_counter() - t0
    print(f"    índice: {len(chunks):,} fragmentos en {seg_index:.0f} s")

    proc_de = [c.procedure for c in chunks]
    nombres = [c.doc_name for c in chunks]

    def puntuar(pregunta: str, proc: str) -> tuple[float, list[str]]:
        qv = np.asarray(next(iter(emb.query_embed([pregunta]))), dtype=np.float32)
        d = _cosine(mat, qv)
        # Misma compuerta de pertinencia que en producción.
        d = np.where(np.array([p in (None, proc) for p in proc_de]), d, -1.0)
        top = np.argsort(-d)[:5]
        return float(d[top[0]]), [nombres[i] for i in top]

    t1 = time.perf_counter()
    pos, aciertos = [], 0
    for proc, q, esperado in RESPONDIBLES:
        s, docs = puntuar(q, proc)
        pos.append(s)
        aciertos += any(esperado.lower() in d.lower() for d in docs)
    # Mastectomía la resuelve la compuerta de procedimiento, no el umbral: no
    # tiene sentido meterla en la medición de separación.
    neg = [puntuar(q, proc)[0] for proc, q in FUERA_DE_CORPUS if proc != "mastectomia"]
    ms = (time.perf_counter() - t1) * 1000 / (len(RESPONDIBLES) + len(neg))

    f1, umbral, rf, af = mejor_f1(pos, neg)
    return {"modelo": modelo.split("/")[-1], "auc": auc(pos, neg), "f1": f1, "umbral": umbral,
            "rechazos_falsos": rf, "afirmaciones_falsas": af,
            "recuperacion": f"{aciertos}/{len(RESPONDIBLES)}", "ms": ms,
            "rango_pos": (min(pos), max(pos)), "rango_neg": (min(neg), max(neg))}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    completo = "--completo" in sys.argv
    chunks = cargar_chunks(completo)
    if chunks is None:
        return 1

    docs = len({c.doc_id for c in chunks})
    print("\nD2 · separación entre preguntas respondibles y ajenas")
    print(f"Corpus: {docs} documentos, {len(chunks):,} fragmentos"
          f"{'' if completo else f' (muestra de {DOCS_POR_PROC} docs/procedimiento)'}")

    filas = [f for m in CANDIDATOS if (f := evaluar(m, chunks))]
    if not filas:
        return 1

    print(f"\n{'='*94}\n  RESUMEN — AUC 1.00 = separación perfecta, 0.50 = azar\n{'='*94}")
    print(f"  {'modelo':<44}{'AUC':>6}{'F1':>6}{'umbral':>8}{'rech.f':>7}"
          f"{'afir.f':>7}{'recup':>7}{'ms/q':>7}")
    for f in filas:
        print(f"  {f['modelo']:<44}{f['auc']:>6.2f}{f['f1']:>6.2f}{f['umbral']:>8.2f}"
              f"{f['rechazos_falsos']:>7}{f['afirmaciones_falsas']:>7}"
              f"{f['recuperacion']:>7}{f['ms']:>7.0f}")

    print("\n  Rangos (si NO se solapan, un umbral separa limpio):")
    for f in filas:
        p, n = f["rango_pos"], f["rango_neg"]
        sep = "SEPARA" if p[0] > n[1] else f"solapa {n[1]-p[0]:+.3f}"
        print(f"    {f['modelo']:<44} con respuesta[{p[0]:.3f}..{p[1]:.3f}] "
              f"sin[{n[0]:.3f}..{n[1]:.3f}]  {sep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
