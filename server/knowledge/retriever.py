"""Recuperación híbrida: denso (embeddings) + disperso (BM25), fusionados con RRF.

Lee los fragmentos activos **frescos en cada consulta**. A la escala de este
corpus el costo es de milisegundos, y a cambio el alta y el borrado se reflejan
al instante sin caché que invalidar: el conocimiento vivo deja de ser una
característica que mantener y pasa a ser una consecuencia de la estructura.

La fusión es RRF —sobre los rangos, no sobre los puntajes— porque las dos señales
viven en escalas distintas y no comparables. Los términos clínicos exactos («pus»,
«fiebre», «39») son justo lo que un modelo denso pequeño diluye y BM25 clava.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi


@dataclass
class Citation:
    doc_id: int
    doc_name: str
    doc_version: int
    section: str
    chunk_id: int
    text: str
    dense: float
    sparse: float
    score: float


def _tok(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _cosine(mat: np.ndarray, vec: np.ndarray) -> np.ndarray:
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    vn = vec / (np.linalg.norm(vec) + 1e-9)
    return mn @ vn


class HybridRetriever:
    def __init__(self, store, embedder, rrf_k: int = 60):
        self.store = store
        self.embedder = embedder
        self.rrf_k = rrf_k

    def query(self, text: str, k: int = 5) -> list[Citation]:
        rows = self.store.active_chunks()
        if not rows:
            return []
        qvec = self.embedder.embed_one(text)
        mat = np.stack([r.embedding for r in rows])
        dense = _cosine(mat, qvec)

        bm = BM25Okapi([_tok(r.text) for r in rows])
        sparse = np.asarray(bm.get_scores(_tok(text)), dtype=np.float32)

        drank = np.empty(len(rows), dtype=int)
        drank[np.argsort(-dense)] = np.arange(len(rows))
        srank = np.empty(len(rows), dtype=int)
        srank[np.argsort(-sparse)] = np.arange(len(rows))
        rrf = 1.0 / (self.rrf_k + drank + 1) + 1.0 / (self.rrf_k + srank + 1)

        return [
            Citation(rows[i].doc_id, rows[i].doc_name, rows[i].doc_version,
                     rows[i].section, rows[i].chunk_id, rows[i].text,
                     float(dense[i]), float(sparse[i]), float(rrf[i]))
            for i in np.argsort(-rrf)[:k]
        ]
