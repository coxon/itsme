"""EventBus facade — ULID + ts injection, emit semantics."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from itsme.core.events import EventBus, EventEnvelope, EventType


@pytest.fixture
def bus(tmp_path: Path) -> Iterator[EventBus]:
    """Fresh bus with a small ring per test."""
    b = EventBus(db_path=tmp_path / "events.db", capacity=10)
    yield b
    b.close()


def test_emit_returns_populated_envelope(bus: EventBus) -> None:
    """emit assigns a ULID + UTC timestamp and returns the envelope."""
    env = bus.emit(
        type=EventType.RAW_CAPTURED,
        source="test",
        payload={"content": "hello"},
    )
    assert isinstance(env, EventEnvelope)
    assert len(env.id) == 26
    assert env.ts.tzinfo == UTC
    assert env.source == "test"
    assert env.payload == {"content": "hello"}


def test_emit_injects_utc_timestamp(bus: EventBus) -> None:
    """Emitted ts is strictly UTC and close to 'now'."""
    before = datetime.now(tz=UTC)
    env = bus.emit(type=EventType.RAW_CAPTURED, source="clock")
    after = datetime.now(tz=UTC)
    assert env.ts.tzinfo == UTC
    assert before <= env.ts <= after


def test_emit_ulids_are_monotonic(bus: EventBus) -> None:
    """Successive emits produce strictly increasing ULIDs."""
    ids = []
    for _ in range(20):
        ids.append(bus.emit(type=EventType.RAW_CAPTURED, source="m").id)
        # Minimal nap so same-millisecond collisions don't confuse assertion.
        time.sleep(0.002)
    assert ids == sorted(ids), "ULIDs must sort monotonically by emission time"
    assert len(set(ids)) == len(ids), "ULIDs must be unique"


def test_emit_defaults_payload_to_empty_dict(bus: EventBus) -> None:
    """None payload becomes {}."""
    env = bus.emit(type=EventType.MEMORY_QUERIED, source="t")
    assert env.payload == {}


def test_emit_isolates_payload_from_caller_mutation(bus: EventBus) -> None:
    """Producer mutating the original dict must NOT affect the emitted event.

    Pydantic v2 doesn't deep-copy dicts on construction, so the bus
    must itself snapshot the payload to keep frozen envelopes truly
    frozen — including nested containers.
    """
    nested: dict[str, Any] = {"tags": ["a", "b"]}
    payload: dict[str, Any] = {"content": "hello", "meta": nested}

    env = bus.emit(type=EventType.RAW_CAPTURED, source="x", payload=payload)

    # Top-level mutation
    payload["content"] = "TAMPERED"
    payload["new_key"] = "leak"
    # Nested mutation
    nested["tags"].append("c")
    nested["sneak"] = "in"

    assert env.payload == {"content": "hello", "meta": {"tags": ["a", "b"]}}


def test_tail_sees_emitted_events(bus: EventBus) -> None:
    """Integration: emit goes through to ring, tail sees it."""
    bus.emit(type=EventType.RAW_CAPTURED, source="a")
    bus.emit(type=EventType.MEMORY_STORED, source="b")
    tail = bus.tail(n=10)
    assert {e.source for e in tail} == {"a", "b"}


def test_since_supports_cursor_workflow(bus: EventBus) -> None:
    """A worker-style polling loop: remember last id, fetch only new."""
    bus.emit(type=EventType.RAW_CAPTURED, source="first")
    cursor = bus.tail(n=1)[0].id  # most recent so far
    bus.emit(type=EventType.RAW_CAPTURED, source="second")
    bus.emit(type=EventType.RAW_CAPTURED, source="third")
    fresh = bus.since(cursor_id=cursor)
    assert [e.source for e in fresh] == ["second", "third"]


def test_count_matches_emits_until_capacity(bus: EventBus) -> None:
    """count tracks emits, capped by ring capacity."""
    for _ in range(15):  # capacity=10
        bus.emit(type=EventType.RAW_CAPTURED, source="x")
    assert bus.count() == 10


def test_type_filter_on_tail(bus: EventBus) -> None:
    """tail filter thread-through to the ring."""
    bus.emit(type=EventType.RAW_CAPTURED, source="r")
    bus.emit(type=EventType.WIKI_PROMOTED, source="w")
    only_wiki = bus.tail(types=[EventType.WIKI_PROMOTED])
    assert [e.source for e in only_wiki] == ["w"]
