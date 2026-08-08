"""Registro auditable de llamadas: cada turno con su decisión y su respaldo.

Lo que tiene que quedar al terminar una llamada no es una transcripción: es un
registro que permita responder **por qué el agente dijo lo que dijo**. Por eso
cada turno guarda junto lo que se dijo, el riesgo asignado, qué capa lo decidió,
qué fragmentos lo sustentan y qué marcó la auditoría de cifras.

Guardarlo por separado —transcripción aquí, decisiones allá— haría que
reconstruir un turno exigiera cruzar tablas por tiempo, que es justo lo que falla
cuando hace falta.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from server.db import connect, serialized

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL NOT NULL,
  ended_at REAL,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER NOT NULL REFERENCES calls(id),
  idx INTEGER NOT NULL,
  patient_text TEXT NOT NULL,
  agent_text TEXT NOT NULL,
  risk TEXT,
  action TEXT,
  source TEXT,
  rationale TEXT,
  citations TEXT,          -- JSON
  grounding_flag TEXT,
  usage TEXT,              -- JSON
  latency_ms TEXT,         -- JSON
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(call_id);
"""


@dataclass
class CallInfo:
    id: int
    started_at: float
    ended_at: float | None
    summary: dict | None
    turns: int


class CallStore:
    def __init__(self, db_path: str = "vera_calls.db"):
        self._db, self._lock = connect(db_path)
        self._db.executescript(SCHEMA)
        self._db.commit()

    @serialized
    def open_call(self) -> int:
        cur = self._db.cursor()
        cur.execute("INSERT INTO calls(started_at) VALUES(?)", (time.time(),))
        self._db.commit()
        return cur.lastrowid

    @serialized
    def record_turn(self, call_id: int, idx: int, patient_text: str, turn) -> None:
        d = turn.decision
        self._db.execute(
            "INSERT INTO turns(call_id,idx,patient_text,agent_text,risk,action,source,"
            "rationale,citations,grounding_flag,usage,latency_ms,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (call_id, idx, patient_text, turn.utterance, d.risk, d.action, d.source,
             d.rationale, json.dumps(turn.citations, ensure_ascii=False),
             turn.grounding_flag, json.dumps(turn.usage),
             json.dumps(turn.latency_ms), time.time()))
        self._db.commit()

    @serialized
    def close_call(self, call_id: int, summary: dict) -> None:
        self._db.execute("UPDATE calls SET ended_at=?, summary=? WHERE id=?",
                         (time.time(), json.dumps(summary, ensure_ascii=False), call_id))
        self._db.commit()

    @serialized
    def calls(self) -> list[CallInfo]:
        rows = self._db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM turns t WHERE t.call_id=c.id) AS n "
            "FROM calls c ORDER BY c.id DESC").fetchall()
        return [CallInfo(r["id"], r["started_at"], r["ended_at"],
                         json.loads(r["summary"]) if r["summary"] else None, r["n"])
                for r in rows]

    @serialized
    def call_detail(self, call_id: int) -> dict | None:
        c = self._db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        if c is None:
            return None
        turns = self._db.execute(
            "SELECT * FROM turns WHERE call_id=? ORDER BY idx", (call_id,)).fetchall()
        return {
            "id": c["id"],
            "started_at": c["started_at"],
            "ended_at": c["ended_at"],
            "summary": json.loads(c["summary"]) if c["summary"] else None,
            "turns": [
                {**{k: r[k] for k in ("idx", "patient_text", "agent_text", "risk",
                                      "action", "source", "rationale", "grounding_flag")},
                 "citations": json.loads(r["citations"]),
                 "usage": json.loads(r["usage"]),
                 "latency_ms": json.loads(r["latency_ms"])}
                for r in turns
            ],
        }

    @serialized
    def close(self) -> None:
        self._db.close()
