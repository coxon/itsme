"""High-level facade over :class:`RingBuffer`.

What it adds on top of the raw ring:

* **ULID generation** — callers don't pick ids; the bus does.
* **UTC timestamp injection** — every emitted event is timestamped at
  emit time, not at producer convenience.
* **Convenience** — ``emit(type, source, payload)`` builds the envelope
  in one call instead of forcing every producer to import pydantic.

This is what MCP tools, workers, and hooks talk to. They never touch
:class:`RingBuffer` directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from itsme.core.events.ringbuf import RingBuffer
from itsme.core.events.schema import EventEnvelope, EventType


class EventBus:
    """Single nervous system — one process, one bus, one ring buffer.

    Args:
        db_path: SQLite ring file.
        capacity: Ring size (defaults to 500, see ARCHITECTURE §5).
    """

    def __init__(self, db_path: Path, capacity: int = 500) -> None:
        self._ring = RingBuffer(db_path=db_path, capacity=capacity)

    def emit(
        self,
        type: EventType,  # noqa: A002 — matches schema field name
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        """Build an envelope (ULID + utcnow) and append to the ring.

        Args:
            type: Event type — must be one of the 6 :class:`EventType`
                members.
            source: Producer id (e.g. ``hook.cc.before-clear``).
            payload: Event-specific dict; ``None`` becomes ``{}``.

        Returns:
            The fully populated envelope (frozen).
        """
        env = EventEnvelope(
            id=str(ULID()),
            ts=datetime.now(tz=UTC),
            type=type,
            source=source,
            payload=payload or {},
            schema_version=1,
        )
        self._ring.append(env)
        return env

    def tail(
        self,
        n: int = 50,
        types: Iterable[EventType] | None = None,
    ) -> list[EventEnvelope]:
        """Most recent *n* events (newest first). See :meth:`RingBuffer.tail`."""
        return self._ring.tail(n=n, types=types)

    def since(
        self,
        cursor_id: str | None = None,
        types: Iterable[EventType] | None = None,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        """Events strictly newer than *cursor_id*. See :meth:`RingBuffer.since`."""
        return self._ring.since(cursor_id=cursor_id, types=types, limit=limit)

    def count(self) -> int:
        """Number of events currently in the ring."""
        return self._ring.count()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._ring.close()
