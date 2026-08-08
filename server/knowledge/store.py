"""Almacén SQLite del conocimiento.

**El borrado es un tombstone**, no un DELETE: marcar `status='deleted'` es
instantáneo y no obliga a reconstruir ningún índice. La visibilidad de cada
fragmento sigue a la de su documento, y la recuperación relee los activos en cada
consulta. Ese par —marcar y releer— es lo que hace que olvidar un protocolo surta
efecto en el turno siguiente, incluso a mitad de llamada.

Subir un documento con un nombre que ya existe no lo pisa: crea una versión nueva
y tombstonea la anterior. Así la trazabilidad de una llamada pasada sigue
apuntando al texto que de verdad se citó entonces.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from server.db import connect, serialized

# Los vectores se guardan en media precisión. El corpus entregado son 10.955
# fragmentos de 1.024 dimensiones: en `float32` el índice pesa 51 MB —por encima
# del límite recomendado de GitHub— y ocupa 44 MB de RAM, porque al arrancar se
# copia entero a memoria.
#
# Medido antes de aplicarlo, sobre el corpus real y 200 consultas: el mayor
# cambio en `max_dense` —la cifra que compara el umbral D3— es 4e-07. El umbral
# se decide con granularidad de 0.01, cuatro órdenes de magnitud por encima, así
# que la decisión de responder o abstenerse no cambia en ningún caso.
#
# `float16` y no `int8` (que pesaría la mitad) porque int8 mueve `max_dense`
# 2e-04, sigue siendo seguro pero exige guardar escala por vector y defender un
# número que ya no es despreciable. La mitad del tamaño, gratis y sin efecto
# medible, es donde está la relación buena.
ALMACEN_VECTOR = np.float16

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',   -- active | deleted
  chunk_count INTEGER NOT NULL DEFAULT 0,
  -- Procedimiento al que pertenece el documento. NULL = documento general,
  -- válido para cualquier paciente: es lo que sube el evaluador por la consola,
  -- y por eso NULL nunca se filtra (ver `HybridRetriever.query`).
  procedure TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  ordinal INTEGER NOT NULL,
  section TEXT,
  text TEXT NOT NULL,
  embedding BLOB NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""


@dataclass
class ActiveChunk:
    chunk_id: int
    doc_id: int
    doc_name: str
    doc_version: int
    section: str
    text: str
    embedding: np.ndarray
    procedure: str | None = None


@dataclass
class DocumentInfo:
    id: int
    name: str
    version: int
    status: str
    chunk_count: int
    created_at: float
    updated_at: float


class KnowledgeStore:
    def __init__(self, db_path: str = "vera_knowledge.db", plantilla: str | None = None):
        self._db, self._lock = connect(db_path, plantilla)
        # Una fila de `chunks` mide ~2,7 KB (vector + texto). Con páginas de
        # 4 KB —el defecto— cada fila se desborda a una página propia que queda
        # a medio llenar, y el índice del corpus pesa 45 MB en vez de 32. Con
        # 2 KB el desperdicio cae al 10% y leer los 10.955 fragmentos cuesta
        # 7 ms más por consulta; con 1 KB o menos se ahorra 1 MB y se pagan 50.
        # Solo surte efecto en una base recién creada: es donde importa, porque
        # es la que produce `scripts/corpus.py` al reconstruir el índice.
        self._db.execute("PRAGMA page_size=2048")
        self._db.executescript(SCHEMA)
        self._db.commit()

    @serialized
    def add_document(self, name: str, sha256: str, chunks, embeddings,
                     procedure: str | None = None) -> int:
        """chunks: list[(section, text)]; embeddings: list[np.ndarray] alineado."""
        now = time.time()
        cur = self._db.cursor()
        prev = cur.execute(
            "SELECT id, version FROM documents WHERE name=? AND status='active' "
            "ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        version = 1
        if prev:
            version = prev["version"] + 1
            cur.execute("UPDATE documents SET status='deleted', updated_at=? WHERE id=?",
                        (now, prev["id"]))
        cur.execute(
            "INSERT INTO documents(name,version,sha256,status,chunk_count,procedure,"
            "created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?)",
            (name, version, sha256, len(chunks), procedure, now, now),
        )
        doc_id = cur.lastrowid
        for i, ((section, text), emb) in enumerate(zip(chunks, embeddings, strict=True)):
            cur.execute(
                "INSERT INTO chunks(doc_id,ordinal,section,text,embedding,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (doc_id, i, section, text, emb.astype(ALMACEN_VECTOR).tobytes(), now),
            )
        self._db.commit()
        return doc_id

    @serialized
    def delete_document(self, doc_id: int) -> bool:
        cur = self._db.cursor()
        cur.execute(
            "UPDATE documents SET status='deleted', updated_at=? WHERE id=? AND status='active'",
            (time.time(), doc_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    @serialized
    def restore_document(self, doc_id: int) -> bool:
        cur = self._db.cursor()
        cur.execute(
            "UPDATE documents SET status='active', updated_at=? WHERE id=? AND status='deleted'",
            (time.time(), doc_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    @serialized
    def active_chunks(self) -> list[ActiveChunk]:
        rows = self._db.execute(
            "SELECT c.id AS chunk_id, c.doc_id, d.name AS doc_name, d.version AS doc_version, "
            "d.procedure, c.section, c.text, c.embedding "
            "FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.status='active' ORDER BY c.doc_id, c.ordinal"
        ).fetchall()
        return [
            ActiveChunk(
                r["chunk_id"], r["doc_id"], r["doc_name"], r["doc_version"],
                r["section"] or "", r["text"],
                # Vista sobre el blob, sin copia: `active_chunks` corre en cada
                # consulta y convertir aquí los 10.955 vectores costaría una
                # asignación de memoria por turno. La promoción a float32 se
                # hace una sola vez, sobre la matriz ya apilada, en el retriever.
                np.frombuffer(r["embedding"], dtype=ALMACEN_VECTOR),
                r["procedure"],
            )
            for r in rows
        ]

    @serialized
    def procedures_present(self) -> set[str]:
        """Procedimientos con al menos un documento activo.

        Permite responder «no tengo material de su cirugía» ANTES de recuperar
        nada, en vez de devolver lo más parecido que haya.
        """
        rows = self._db.execute(
            "SELECT DISTINCT procedure FROM documents "
            "WHERE status='active' AND procedure IS NOT NULL"
        ).fetchall()
        return {r["procedure"] for r in rows}

    @serialized
    def list_documents(self, include_deleted: bool = True) -> list[DocumentInfo]:
        q = "SELECT * FROM documents"
        if not include_deleted:
            q += " WHERE status='active'"
        q += " ORDER BY updated_at DESC"
        return [
            DocumentInfo(r["id"], r["name"], r["version"], r["status"],
                         r["chunk_count"], r["created_at"], r["updated_at"])
            for r in self._db.execute(q).fetchall()
        ]

    @serialized
    def close(self) -> None:
        self._db.close()
