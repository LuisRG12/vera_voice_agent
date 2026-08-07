"""Embeddings locales con fastembed (ONNX, sin torch). Modelo intercambiable.

**Documento y consulta no se embeben igual.** Los modelos entrenados para
recuperación esperan los prefijos `query:` y `passage:`, y sin ellos rinden por
debajo de lo que pueden. fastembed los aplica por modelo en `query_embed()` y
`passage_embed()`; usar `embed()` para todo desaprovecha justamente lo que
distingue a un modelo de recuperación de uno de paráfrasis.

Qué modelo concreto se usa es la decisión D2 de `docs/arquitectura.md` y se
resuelve midiendo, no por reputación.
"""
from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

from server.config import settings


class Embedder:
    def __init__(self, model: str | None = None):
        self.name = model or settings.embedding_model
        self._model = TextEmbedding(model_name=self.name)

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        """Fragmentos de documento (lado 'passage')."""
        return [np.asarray(v, dtype=np.float32)
                for v in self._model.passage_embed(list(texts))]

    def embed_one(self, text: str) -> np.ndarray:
        """Consulta del paciente (lado 'query')."""
        return np.asarray(next(iter(self._model.query_embed([text]))), dtype=np.float32)
