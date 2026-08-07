"""Acceso serializado a SQLite, compartido por los stores.

Los stores viven en el ciclo de vida del servidor y los comparten TODAS las
llamadas, que corren sus operaciones en hilos distintos (`asyncio.to_thread`).
Abrir la conexión con `check_same_thread=False` permite ese uso pero **no lo hace
seguro**: solo desactiva la comprobación.

Se serializa el método completo y no cada sentencia: cada método de store es una
unidad de trabajo (execute + commit, o execute + `lastrowid`), y bloquear por
sentencia dejaría la ventana abierta entre el INSERT y el `lastrowid` que le
corresponde.

El costo es que las lecturas también se serializan. Es aceptable: son operaciones
de milisegundos sobre un índice pequeño, y perder un turno del registro clínico
no lo es.
"""
from __future__ import annotations

import functools
import sqlite3
import threading
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)


def connect(db_path: str, plantilla: str | None = None
            ) -> tuple[sqlite3.Connection, threading.RLock]:
    """Devuelve la conexión y el candado que debe protegerla.

    `plantilla` copia una base de disco a la conexión recién abierta. Sirve para
    arrancar con un índice ya construido **en memoria**: la sesión empieza con
    todo el conocimiento disponible y a la vez limpia, sin arrastrar lo que se
    subió en la ejecución anterior.
    """
    db = sqlite3.connect(db_path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    if plantilla:
        origen = sqlite3.connect(plantilla)
        try:
            origen.backup(db)
        finally:
            origen.close()
    return db, threading.RLock()


def serialized(method: F) -> F:
    """Serializa el método completo sobre `self._lock`."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper  # type: ignore[return-value]
