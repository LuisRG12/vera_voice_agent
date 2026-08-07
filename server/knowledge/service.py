"""Orquesta parseo, troceado, embeddings, almacenamiento y recuperación.

Es la única pieza que las demás capas conocen: el diálogo pide conocimiento aquí
y no sabe si detrás hay SQLite, BM25 o un modelo ONNX. Cambiar cualquiera de esos
no debería tocar una línea del agente.

Métodos síncronos (operaciones cortas sobre SQLite y CPU). El servidor async los
invoca con `asyncio.to_thread`.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from server.config import settings
from server.knowledge.chunker import chunk_document, parse_bytes, parse_file
from server.knowledge.embedder import Embedder
from server.knowledge.retriever import Citation, HybridRetriever
from server.knowledge.store import KnowledgeStore

# Palabras funcionales frecuentes que no cuentan como evidencia léxica: aparecen
# en cualquier texto clínico y por tanto no distinguen un fragmento de otro.
_STOP = {
    "para", "como", "pero", "porque", "cuando", "cuanto", "donde", "esto", "esta",
    "este", "tengo", "puedo", "puede", "debo", "hacer", "sobre", "segun", "desde",
    "hasta", "despues", "quiero", "necesito", "tener", "estoy", "siento", "muy",
    "mas", "algo", "cosa", "hola",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúñü]+", text.lower())
            if len(w) >= 4 and w not in _STOP}


class KnowledgeService:
    def __init__(self, db_path: str | None = None, model: str | None = None,
                 min_evidence: float | None = None, plantilla: str | None = None):
        self.store = KnowledgeStore(db_path or settings.knowledge_db, plantilla)
        self.embedder = Embedder(model)
        self.retriever = HybridRetriever(self.store, self.embedder)
        self.min_evidence = settings.min_evidence if min_evidence is None else min_evidence

    # ---------- alta y baja ----------
    def add_text(self, name: str, text: str, procedure: str | None = None) -> int:
        chunks = chunk_document(text)
        if not chunks:
            raise ValueError("documento vacío tras el troceado")
        embeddings = self.embedder.embed([c[1] for c in chunks])
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self.store.add_document(name, sha, chunks, embeddings, procedure)

    def add_file(self, path: str, procedure: str | None = None) -> int:
        return self.add_text(Path(path).name, parse_file(path), procedure)

    def add_bytes(self, name: str, data: bytes) -> int:
        """Ingiere un archivo subido por la consola, sin tocar el disco.

        Sin procedimiento a propósito: lo que sube el evaluador debe verlo
        cualquier paciente. No podemos adivinar a qué cirugía pertenece su
        documento de prueba, y equivocarnos lo dejaría invisible.
        """
        return self.add_text(name, parse_bytes(name, data))

    def delete(self, doc_id: int) -> bool:
        return self.store.delete_document(doc_id)

    def restore(self, doc_id: int) -> bool:
        return self.store.restore_document(doc_id)

    def documents(self, include_deleted: bool = True):
        return self.store.list_documents(include_deleted)

    # ---------- consulta ----------
    def procedures_present(self) -> set[str]:
        """Procedimientos con documentos activos (para la compuerta de pertinencia)."""
        return self.store.procedures_present()

    def query(self, text: str, k: int = 5, procedimiento: str | None = None) -> dict:
        """Fragmentos recuperados y si constituyen evidencia suficiente.

        La evidencia es híbrida a propósito: **semántica** (el mejor puntaje
        denso supera el umbral) **o léxica** (la consulta comparte términos
        clínicos exactos con un fragmento del top). Los signos de alarma —pus,
        fiebre, sangrado— son palabras exactas que un modelo denso pequeño
        diluye; el solapamiento léxico las rescata como evidencia.

        Dónde va el umbral es la decisión D3 de `docs/arquitectura.md`.
        """
        citations: list[Citation] = self.retriever.query(text, k, procedimiento)
        max_dense = max((c.dense for c in citations), default=0.0)
        qwords = _content_words(text)
        lexical = max((len(qwords & _content_words(c.text)) for c in citations[:3]), default=0)
        return {
            "has_evidence": max_dense >= self.min_evidence or lexical >= 2,
            "max_dense": max_dense,
            "lexical_overlap": lexical,
            "citations": citations,
        }

    def close(self) -> None:
        self.store.close()
