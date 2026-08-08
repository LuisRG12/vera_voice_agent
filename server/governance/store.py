"""Registro append-only de alertas e invocaciones de herramientas.

**Append-only y no un estado mutable.** Una alerta clínica no se «resuelve»
borrándola: se acusa recibo, y queda quién lo hizo y cuándo. Si el registro se
pudiera editar, no serviría para reconstruir qué supo el equipo clínico y en qué
momento —que es exactamente lo que hay que poder responder cuando algo sale mal—.

El acuse de recibo es lo que cierra el ciclo con una persona. Sin él, «el sistema
alertó» es una afirmación que nadie puede verificar.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from server.db import connect, serialized

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER,
  severity TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence TEXT NOT NULL,          -- JSON: qué lo motivó
  created_at REAL NOT NULL,
  acked_by TEXT,                   -- NULL mientras nadie la haya visto
  acked_note TEXT,
  acked_at REAL
);
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER,
  tool TEXT NOT NULL,
  phase TEXT,
  payload TEXT,
  allowed INTEGER NOT NULL,
  reason TEXT,
  created_at REAL NOT NULL
);
"""


@dataclass
class Alert:
    id: int
    call_id: int | None
    severity: str
    reason: str
    evidence: dict
    created_at: float
    acked_by: str | None
    acked_note: str | None
    acked_at: float | None

    @property
    def activa(self) -> bool:
        return self.acked_by is None


class GovernanceStore:
    def __init__(self, db_path: str = "vera_governance.db"):
        self._db, self._lock = connect(db_path)
        self._db.executescript(SCHEMA)
        self._db.commit()

    @serialized
    def raise_alert(self, call_id: int | None, severity: str, reason: str,
                    evidence: dict) -> int:
        cur = self._db.cursor()
        cur.execute(
            "INSERT INTO alerts(call_id,severity,reason,evidence,created_at) VALUES(?,?,?,?,?)",
            (call_id, severity, reason, json.dumps(evidence, ensure_ascii=False), time.time()))
        self._db.commit()
        return cur.lastrowid

    @serialized
    def ack(self, alert_id: int, who: str, note: str = "") -> bool:
        """Acusa recibo. Devuelve False si no existe o si ya estaba acusada.

        No se puede acusar dos veces: el primer acuse es el que cuenta y
        sobrescribirlo borraría quién la atendió realmente.
        """
        cur = self._db.cursor()
        cur.execute(
            "UPDATE alerts SET acked_by=?, acked_note=?, acked_at=? "
            "WHERE id=? AND acked_by IS NULL",
            (who, note, time.time(), alert_id))
        self._db.commit()
        return cur.rowcount > 0

    @serialized
    def alerts(self, solo_activas: bool = False) -> list[Alert]:
        q = "SELECT * FROM alerts"
        if solo_activas:
            q += " WHERE acked_by IS NULL"
        q += " ORDER BY id DESC"
        return [
            Alert(r["id"], r["call_id"], r["severity"], r["reason"],
                  json.loads(r["evidence"]), r["created_at"],
                  r["acked_by"], r["acked_note"], r["acked_at"])
            for r in self._db.execute(q).fetchall()
        ]

    @serialized
    def log_tool(self, call_id: int | None, tool: str, phase: str,
                 payload: dict, allowed: bool, reason: str = "") -> None:
        self._db.execute(
            "INSERT INTO tool_calls(call_id,tool,phase,payload,allowed,reason,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (call_id, tool, phase, json.dumps(payload, ensure_ascii=False),
             int(allowed), reason, time.time()))
        self._db.commit()

    @serialized
    def tool_calls(self, call_id: int | None = None) -> list[dict]:
        q = "SELECT * FROM tool_calls"
        args: tuple = ()
        if call_id is not None:
            q += " WHERE call_id=?"
            args = (call_id,)
        q += " ORDER BY id"
        return [dict(r) for r in self._db.execute(q, args).fetchall()]

    @serialized
    def close(self) -> None:
        self._db.close()
