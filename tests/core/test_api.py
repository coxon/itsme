"""Memory orchestrator — remember / ask / status (T1.10–T1.12)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core import AskResult, Memory, RememberResult, StatusResult
from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.events import EventBus, EventType


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[Memory]:
    """Fresh Memory with bounded ring + in-memory adapter."""
    bus = EventBus(db_path=tmp_path / "events.db", capacity=100)
    m = Memory(bus=bus, adapter=InMemoryMemPalaceAdapter(), project="testproj")
    yield m
    m.close()


# --------------------------------------------------------- remember
def test_remember_returns_populated_result(memory: Memory) -> None:
    """remember returns RememberResult with all ids filled in."""
    res = memory.remember("first memory", kind="fact")
    assert isinstance(res, RememberResult)
    assert len(res.id) == 26
    assert len(res.drawer_id) == 26
    assert res.wing == "wing_testproj"
    assert res.room == "room_facts"
    assert res.routed_to == [f"mempalace:{res.drawer_id}"]
    assert len(res.stored_event_id) == 26


def test_remember_emits_two_events(memory: Memory) -> None:
    """One remember call → raw.captured + memory.stored."""
    memory.remember("hello", kind="fact")
    tail = memory.status(scope="recent", limit=10).events
    types = [e.type for e in tail]
    assert "raw.captured" in types
    assert "memory.stored" in types


def test_remember_without_kind_uses_general_room(memory: Memory) -> None:
    """Omitted kind routes to ``room_general``."""
    res = memory.remember("no hint")
    assert res.room == "room_general"


def test_remember_unknown_kind_falls_back_to_general(memory: Memory) -> None:
    """Memory orchestrator falls back; the *tool* layer rejects junk."""
    res = memory.remember("free-form", kind="weird")  # type: ignore[arg-type]
    assert res.room == "room_general"


def test_remember_rejects_empty_content(memory: Memory) -> None:
    """Whitespace-only content is rejected."""
    with pytest.raises(ValueError):
        memory.remember("   ")


def test_remember_writes_visible_drawer(memory: Memory) -> None:
    """A remembered drawer is then findable via ask."""
    memory.remember("zeppelin float overhead", kind="fact")
    res = memory.ask("zeppelin")
    assert res.sources, "expected at least one hit"
    assert "zeppelin" in res.sources[0].content.lower()


# --------------------------------------------------------- ask
def test_ask_returns_ask_result(memory: Memory) -> None:
    """ask returns the typed AskResult."""
    memory.remember("abc def", kind="fact")
    res = memory.ask("abc")
    assert isinstance(res, AskResult)
    assert res.sources
    assert res.sources[0].kind == "verbatim"
    assert res.sources[0].score > 0
    assert res.promoted is False
    assert res.promotion_event_id is None


def test_ask_emits_memory_queried(memory: Memory) -> None:
    """Each ask emits exactly one memory.queried event."""
    memory.ask("anything")
    qs = memory.status(scope="recent", limit=10, types=[EventType.MEMORY_QUERIED]).events
    assert len(qs) == 1


def test_ask_rejects_empty_question(memory: Memory) -> None:
    """Whitespace-only question is rejected."""
    with pytest.raises(ValueError):
        memory.ask("   ")


def test_ask_rejects_non_positive_limit(memory: Memory) -> None:
    """limit must be positive."""
    with pytest.raises(ValueError):
        memory.ask("q", limit=0)


@pytest.mark.parametrize("mode", ["auto", "wiki", "now"])
def test_ask_unsupported_modes_raise(memory: Memory, mode: str) -> None:
    """v0.0.1 only implements 'verbatim'."""
    from typing import Literal, cast

    with pytest.raises(NotImplementedError):
        memory.ask("q", mode=cast(Literal["auto", "wiki", "now"], mode))


def test_ask_scopes_to_project_wing_by_default(memory: Memory) -> None:
    """Default scope_to_project=True excludes other wings.

    Constructed by writing directly to the adapter under a different
    wing (simulating another project sharing the MP instance).
    """
    memory.remember("project-local memory", kind="fact")
    # Simulate another project's drawer — write to adapter directly.
    # Access via _adapter is intentional for this test.
    other = memory._adapter  # noqa: SLF001
    other.write(content="other project memory", wing="wing_other", room="room_x")

    res = memory.ask("memory")
    assert res.sources, "expected at least one hit from the project's wing"
    assert all("project-local" in s.content for s in res.sources)


def test_ask_can_search_all_wings(memory: Memory) -> None:
    """scope_to_project=False removes the wing filter."""
    memory.remember("local memory item", kind="fact")
    other = memory._adapter  # noqa: SLF001
    other.write(content="other wing memory", wing="wing_other", room="room_x")

    res = memory.ask("memory", scope_to_project=False)
    contents = {s.content for s in res.sources}
    assert "local memory item" in contents
    assert "other wing memory" in contents


# --------------------------------------------------------- status
def test_status_recent_returns_typed_result(memory: Memory) -> None:
    """status returns a StatusResult with newest-first events."""
    memory.remember("a", kind="fact")
    memory.remember("b", kind="fact")
    res = memory.status(scope="recent", limit=10)
    assert isinstance(res, StatusResult)
    assert res.scope == "recent"
    assert res.count >= 4  # 2 raw + 2 stored


def test_status_filters_by_event_type(memory: Memory) -> None:
    """``types=`` filter narrows the feed."""
    memory.remember("only", kind="fact")
    res = memory.status(scope="recent", types=[EventType.RAW_CAPTURED])
    assert {e.type for e in res.events} == {"raw.captured"}


def test_status_rejects_non_positive_limit(memory: Memory) -> None:
    """limit must be positive."""
    with pytest.raises(ValueError):
        memory.status(limit=0)


# ============================================================
# T1.19 — content-hash dedup via the public Memory API
# ============================================================


def test_remember_stamps_content_hash_into_raw_captured(memory: Memory) -> None:
    """``Memory.remember`` mirrors content_hash + producer_kind onto raw.captured.

    Pins the wire contract: T1.19 dedup downstream depends on every
    ``Memory.remember`` call carrying the hash so the router can
    short-circuit cross-producer collisions.
    """
    from itsme.core.dedup import content_hash as _hash
    from itsme.core.dedup import producer_kind_from_source as _kind

    memory.remember("decided to ship", kind="decision")
    raws = memory.status(scope="recent", limit=20, types=[EventType.RAW_CAPTURED]).events
    assert len(raws) == 1, "expected exactly one raw.captured"
    payload = raws[0].payload
    assert payload["content_hash"] == _hash("decided to ship")
    assert payload["producer_kind"] == _kind("explicit")


def test_remember_twice_same_content_returns_same_drawer(memory: Memory) -> None:
    """A double remember of identical content surfaces one drawer.

    Idempotent ``remember`` is what callers get for free now that the
    router dedups — the second call returns the *original* drawer_id
    so downstream graph / promotion tools have a stable handle.
    """
    a = memory.remember("decided to roll back", kind="decision")
    b = memory.remember("decided to roll back", kind="decision")
    assert b.drawer_id == a.drawer_id
    assert b.wing == a.wing
    assert b.room == a.room
    # ``stored_event_id`` points back at the *original* memory.stored
    # event (via the curated dedup link in ``_latest_stored_event_id``).
    assert b.stored_event_id == a.stored_event_id


def test_remember_dedup_emits_memory_curated(memory: Memory) -> None:
    """The dedup short-circuit logs a memory.curated event for observability."""
    memory.remember("idempotent fact", kind="fact")
    memory.remember("idempotent fact", kind="fact")

    curated = memory.status(scope="recent", limit=20, types=[EventType.MEMORY_CURATED]).events
    assert len(curated) == 1
    assert curated[0].payload["reason"] == "dedup"
    assert curated[0].payload["producer_kind"] == "explicit"


def test_remember_different_content_writes_two_drawers(memory: Memory) -> None:
    """Sanity: distinct content does not get accidentally deduped."""
    a = memory.remember("alpha decision", kind="decision")
    b = memory.remember("beta decision", kind="decision")
    assert a.drawer_id != b.drawer_id
    stored = memory.status(scope="recent", limit=20, types=[EventType.MEMORY_STORED]).events
    assert len(stored) == 2
    curated = memory.status(scope="recent", limit=20, types=[EventType.MEMORY_CURATED]).events
    assert curated == []


def test_remember_dedups_against_prior_hook_capture(memory: Memory) -> None:
    """Cross-producer: a hook capture seeds dedup; explicit remember surfaces it.

    Simulates the real-session shape — a SessionEnd salvage already
    ran (somehow earlier in this process) and stored "decided X". The
    user later runs ``remember("decided X")`` explicitly. With T1.19
    the explicit call returns the prior drawer rather than writing a
    second one.
    """
    from itsme.core.dedup import content_hash as _hash
    from itsme.core.dedup import producer_kind_from_source as _kind

    bus = memory._bus  # noqa: SLF001
    # Seed a hook-style raw.captured + run the router fast-path on it
    # via the internal router so we don't need the consume_loop.
    hook_raw = bus.emit(
        type=EventType.RAW_CAPTURED,
        source="hook:before-exit",
        payload={
            "content": "decided to roll forward",
            "kind": "decision",
            "content_hash": _hash("decided to roll forward"),
            "producer_kind": _kind("hook:before-exit"),
        },
    )
    hook_write = memory._router.route_and_store(hook_raw)  # noqa: SLF001

    # Now the explicit remember of the same content.
    res = memory.remember("decided to roll forward", kind="decision")

    # Surfaces the hook's drawer.
    assert res.drawer_id == hook_write.drawer_id
    # Exactly one memory.stored, one memory.curated.
    stored = memory.status(scope="recent", limit=20, types=[EventType.MEMORY_STORED]).events
    curated = memory.status(scope="recent", limit=20, types=[EventType.MEMORY_CURATED]).events
    assert len(stored) == 1
    assert len(curated) == 1
    assert curated[0].payload["producer_kind"] == "explicit"
