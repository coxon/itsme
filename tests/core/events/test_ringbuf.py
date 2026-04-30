"""Ring-buffer behavior — eviction, filtering, ordering, concurrency."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ulid import ULID

from itsme.core.events.ringbuf import RingBuffer
from itsme.core.events.schema import EventEnvelope, EventType


def _make_event(
    evt_type: EventType = EventType.RAW_CAPTURED,
    source: str = "test",
) -> EventEnvelope:
    """Build a fresh envelope with a real ULID."""
    return EventEnvelope(
        id=str(ULID()),
        ts=datetime.now(tz=UTC),
        type=evt_type,
        source=source,
        payload={"note": "hello"},
    )


@pytest.fixture
def ring(tmp_path: Path) -> Iterator[RingBuffer]:
    """A small ring (capacity=3) so eviction fires quickly."""
    rb = RingBuffer(db_path=tmp_path / "events.db", capacity=3)
    yield rb
    rb.close()


def test_append_then_count(ring: RingBuffer) -> None:
    """append increments count."""
    ring.append(_make_event())
    ring.append(_make_event())
    assert ring.count() == 2


def test_tail_returns_newest_first(ring: RingBuffer) -> None:
    """tail(n) is ordered newest→oldest."""
    first = _make_event(source="first")
    ring.append(first)
    second = _make_event(source="second")
    ring.append(second)
    tail = ring.tail(n=10)
    assert [e.source for e in tail] == ["second", "first"]


def test_tail_respects_limit(ring: RingBuffer) -> None:
    """tail honors the n argument."""
    for _ in range(3):
        ring.append(_make_event())
    assert len(ring.tail(n=1)) == 1
    assert len(ring.tail(n=100)) == 3


def test_capacity_eviction(ring: RingBuffer) -> None:
    """Once past capacity, the oldest entry is evicted."""
    a = _make_event(source="a")
    b = _make_event(source="b")
    c = _make_event(source="c")
    d = _make_event(source="d")
    for e in (a, b, c, d):
        ring.append(e)
    assert ring.count() == 3
    sources = {e.source for e in ring.tail(n=10)}
    assert sources == {"b", "c", "d"}
    assert "a" not in sources


def test_type_filter_on_tail(ring: RingBuffer) -> None:
    """tail(types=...) only returns matching types."""
    ring.append(_make_event(evt_type=EventType.RAW_CAPTURED, source="raw"))
    ring.append(_make_event(evt_type=EventType.MEMORY_STORED, source="stored"))
    ring.append(_make_event(evt_type=EventType.MEMORY_QUERIED, source="query"))
    only_stored = ring.tail(n=10, types=[EventType.MEMORY_STORED])
    assert [e.source for e in only_stored] == ["stored"]


def test_empty_type_filter_returns_nothing(ring: RingBuffer) -> None:
    """An empty filter iterable means 'no type matches'."""
    ring.append(_make_event())
    assert ring.tail(n=10, types=[]) == []


def test_empty_type_filter_with_cursor_does_not_misbind(
    ring: RingBuffer,
) -> None:
    """Regression: ``since(cursor_id=X, types=[])`` must not raise.

    Earlier ``_build_filter`` returned ``WHERE 1=0`` while still leaving
    the cursor_id in the params list, causing a sqlite binding-count
    mismatch.
    """
    a = _make_event(source="a")
    ring.append(a)
    ring.append(_make_event(source="b"))
    # Should return [] cleanly, not raise sqlite3.ProgrammingError.
    assert ring.since(cursor_id=a.id, types=[]) == []
    assert ring.tail(n=5, types=[]) == []


def test_since_oldest_first_strictly_after_cursor(ring: RingBuffer) -> None:
    """since(cursor_id) returns events whose id > cursor, ordered ascending."""
    a = _make_event(source="a")
    ring.append(a)
    b = _make_event(source="b")
    ring.append(b)
    c = _make_event(source="c")
    ring.append(c)
    after_a = ring.since(cursor_id=a.id)
    assert [e.source for e in after_a] == ["b", "c"]


def test_since_none_cursor_returns_everything(ring: RingBuffer) -> None:
    """None cursor is 'from the beginning of retained history'."""
    ring.append(_make_event(source="x"))
    ring.append(_make_event(source="y"))
    everything = ring.since(cursor_id=None)
    assert [e.source for e in everything] == ["x", "y"]


def test_since_type_filter(ring: RingBuffer) -> None:
    """since honors type filter in the same way as tail."""
    a = _make_event(evt_type=EventType.RAW_CAPTURED, source="raw")
    ring.append(a)
    b = _make_event(evt_type=EventType.WIKI_PROMOTED, source="wiki")
    ring.append(b)
    c = _make_event(evt_type=EventType.RAW_CAPTURED, source="raw2")
    ring.append(c)
    raws = ring.since(cursor_id=None, types=[EventType.RAW_CAPTURED])
    assert [e.source for e in raws] == ["raw", "raw2"]


def test_payload_roundtrip(ring: RingBuffer) -> None:
    """Complex payload JSON-round-trips through sqlite unchanged."""
    env = EventEnvelope(
        id=str(ULID()),
        ts=datetime.now(tz=UTC),
        type=EventType.MEMORY_STORED,
        source="test",
        payload={
            "drawer_id": "abc",
            "tags": ["x", "y"],
            "nested": {"a": 1, "b": [2, 3]},
            "unicode": "记忆 🧠",
        },
    )
    ring.append(env)
    (back,) = ring.tail(n=1)
    assert back.payload == env.payload


def test_rejects_zero_capacity(tmp_path: Path) -> None:
    """capacity must be positive."""
    with pytest.raises(ValueError, match="capacity"):
        RingBuffer(db_path=tmp_path / "x.db", capacity=0)


def test_creates_parent_directory(tmp_path: Path) -> None:
    """Missing parent directories are created automatically."""
    target = tmp_path / "nested" / "deep" / "events.db"
    rb = RingBuffer(db_path=target, capacity=5)
    try:
        rb.append(_make_event())
        assert target.exists()
    finally:
        rb.close()


def test_concurrent_appends_are_safe(tmp_path: Path) -> None:
    """Many threads appending simultaneously — no corruption, no loss."""
    rb = RingBuffer(db_path=tmp_path / "events.db", capacity=1000)
    try:
        n_threads = 8
        per_thread = 25
        barrier = threading.Barrier(n_threads)

        def worker() -> None:
            barrier.wait()
            for _ in range(per_thread):
                rb.append(_make_event())

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert rb.count() == n_threads * per_thread
    finally:
        rb.close()
