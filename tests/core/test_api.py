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
