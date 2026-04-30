"""SQLite-backed ring buffer for the EventBus.

* Capacity-bounded — once full, oldest events are evicted on every
  append. Ordering by ULID id == ordering by emission time, so eviction
  is a single ``DELETE ... ORDER BY id DESC LIMIT -1 OFFSET capacity``.
* Single ``sqlite3`` connection per instance, serialized via a
  :class:`threading.Lock`. This is plenty fast for human-typing-speed
  event volumes and avoids the foot-guns of per-call connections.
* WAL journal mode so a future external reader (e.g. an `itsme events
  tail` CLI) can observe live events without blocking writes.

Schema::

    CREATE TABLE events (
        id             TEXT    PRIMARY KEY,    -- ULID
        ts             TEXT    NOT NULL,       -- ISO-8601 UTC
        type           TEXT    NOT NULL,
        source         TEXT    NOT NULL,
        payload        TEXT    NOT NULL,       -- JSON
        schema_version INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX idx_events_type_id ON events(type, id);
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from itsme.core.events.schema import EventEnvelope, EventType


class RingBuffer:
    """SQLite-backed bounded event buffer.

    Args:
        db_path: Where the SQLite file lives. Parent directory is
            created if missing.
        capacity: Maximum number of events kept; older ones evicted on
            insert.
    """

    def __init__(self, db_path: Path, capacity: int = 500) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        self._db_path = db_path
        self._capacity = capacity
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we batch under our lock
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id             TEXT    PRIMARY KEY,
                    ts             TEXT    NOT NULL,
                    type           TEXT    NOT NULL,
                    source         TEXT    NOT NULL,
                    payload        TEXT    NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_events_type_id
                    ON events(type, id);
                """
            )

    @property
    def capacity(self) -> int:
        """Configured upper bound on retained events."""
        return self._capacity

    def append(self, env: EventEnvelope) -> str:
        """Insert an event and evict overflow.

        Returns:
            The event's ULID (same as ``env.id``, returned for ergonomics).
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (id, ts, type, source, payload, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    env.id,
                    env.ts.isoformat(),
                    env.type.value,
                    env.source,
                    json.dumps(env.payload),
                    env.schema_version,
                ),
            )
            # Evict everything past capacity (ordered newest-first by ULID).
            self._conn.execute(
                "DELETE FROM events WHERE id IN ("
                "  SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET ?"
                ")",
                (self._capacity,),
            )
        return env.id

    def tail(
        self,
        n: int = 50,
        types: Iterable[EventType] | None = None,
    ) -> list[EventEnvelope]:
        """Return the *n* most recent events, newest first.

        Args:
            n: Maximum events to return.
            types: Optional filter — keep only events whose type is in
                this iterable.
        """
        where, params = _build_filter(cursor_id=None, types=types)
        params.append(n)
        with self._lock:
            cur = self._conn.execute(
                f"SELECT id, ts, type, source, payload, schema_version "
                f"FROM events {where} ORDER BY id DESC LIMIT ?",
                params,
            )
            rows = cur.fetchall()
        return [_row_to_envelope(r) for r in rows]

    def since(
        self,
        cursor_id: str | None,
        types: Iterable[EventType] | None = None,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        """Return events strictly newer than *cursor_id*, oldest first.

        For workers polling the bus: pass the last id you handled, get
        back the next batch.

        Args:
            cursor_id: Last seen event id; pass ``None`` to start from
                the very first stored event.
            types: Optional type filter.
            limit: Maximum events per call.
        """
        where, params = _build_filter(cursor_id=cursor_id, types=types)
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(
                f"SELECT id, ts, type, source, payload, schema_version "
                f"FROM events {where} ORDER BY id ASC LIMIT ?",
                params,
            )
            rows = cur.fetchall()
        return [_row_to_envelope(r) for r in rows]

    def count(self) -> int:
        """Total events currently retained."""
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM events")
            (n,) = cur.fetchone()
        return int(n)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()


def _build_filter(
    cursor_id: str | None,
    types: Iterable[EventType] | None,
) -> tuple[str, list[Any]]:
    """Build a WHERE clause for the events table.

    Returns:
        ``(where_clause, params)`` — *where_clause* is empty string if no
        filters apply.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if cursor_id is not None:
        clauses.append("id > ?")
        params.append(cursor_id)
    if types is not None:
        type_list = list(types)
        if not type_list:
            # Empty iterable means "no types match" — short-circuit.
            return "WHERE 1=0", params
        placeholders = ",".join("?" for _ in type_list)
        clauses.append(f"type IN ({placeholders})")
        params.extend(t.value for t in type_list)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _row_to_envelope(row: tuple[Any, ...]) -> EventEnvelope:
    """Hydrate a sqlite row into an :class:`EventEnvelope`."""
    return EventEnvelope(
        id=row[0],
        ts=datetime.fromisoformat(row[1]),
        type=EventType(row[2]),
        source=row[3],
        payload=json.loads(row[4]),
        schema_version=row[5],
    )
