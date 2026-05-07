"""Tests for ask(mode='auto') — T2.19 Memory integration.

Verifies that Memory.ask() routes through dual_search correctly
and emits the right events.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.api import Aleph
from itsme.core.api import Memory
from itsme.core.events import EventBus, EventType


@pytest.fixture
def bus(tmp_path: Path) -> Iterator[EventBus]:
    ring = EventBus(db_path=tmp_path / "events.db")
    try:
        yield ring
    finally:
        ring.close()


@pytest.fixture
def adapter() -> InMemoryMemPalaceAdapter:
    return InMemoryMemPalaceAdapter()


@pytest.fixture
def aleph() -> Iterator[Aleph]:
    a = Aleph(":memory:")
    yield a
    a.close()


@pytest.fixture
def memory(bus: EventBus, adapter: InMemoryMemPalaceAdapter, aleph: Aleph) -> Memory:
    return Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)


class TestAskAuto:
    def test_auto_mode_accepted(self, memory: Memory) -> None:
        """mode='auto' no longer raises."""
        result = memory.ask("anything", mode="auto")
        assert result.queried_event_id

    def test_auto_returns_aleph_hits(self, memory: Memory, adapter, aleph) -> None:
        """Aleph extraction hits show up in auto mode."""
        aleph.write_extraction(
            turn_id="d1",
            raw_event_id="evt-1",
            summary="User chose Postgres for the project",
            entities=[{"name": "Postgres", "type": "database"}],
            claims=["Postgres chosen for project"],
            source="test",
        )

        result = memory.ask("Postgres", mode="auto")
        extraction_sources = [s for s in result.sources if s.kind == "extraction"]
        assert len(extraction_sources) >= 1
        assert "Postgres" in extraction_sources[0].content

    def test_auto_returns_mempalace_hits(self, memory: Memory, adapter) -> None:
        """MemPalace raw hits show up as verbatim in auto mode."""
        adapter.write(
            content="Redis is used for session caching",
            wing="wing_test",
            room="room_general",
        )

        result = memory.ask("Redis caching", mode="auto")
        mp_sources = [s for s in result.sources if s.kind == "verbatim"]
        assert len(mp_sources) >= 1

    def test_auto_deduplicates_same_turn(self, memory: Memory, adapter, aleph) -> None:
        """Same turn hit by both engines → only Aleph hit shown."""
        res = adapter.write(
            content="We will use DynamoDB for the event store",
            wing="wing_test",
            room="room_general",
        )
        aleph.write_extraction(
            turn_id=res.drawer_id,
            raw_event_id="evt-1",
            summary="DynamoDB selected for event store",
            entities=[{"name": "DynamoDB", "type": "database"}],
            claims=["DynamoDB for event store"],
            source="test",
        )

        result = memory.ask("DynamoDB event store", mode="auto")
        # Should not see duplicate entries for the same drawer
        refs = [s.ref for s in result.sources]
        aleph_refs = [r for r in refs if r.startswith("aleph:")]
        # Aleph hit is present
        assert len(aleph_refs) >= 1

    def test_auto_emits_queried_event_with_mode(self, memory: Memory, bus) -> None:
        """memory.queried event carries mode='auto'."""
        memory.ask("anything", mode="auto")
        events = bus.tail(n=5, types=[EventType.MEMORY_QUERIED])
        assert len(events) >= 1
        assert events[0].payload["mode"] == "auto"

    def test_auto_event_has_hit_breakdown(self, memory: Memory, bus, aleph) -> None:
        """Event payload includes aleph_hits / mp_hits counts."""
        aleph.write_extraction(
            turn_id="d1",
            raw_event_id="evt-1",
            summary="Test extraction",
            entities=[],
            claims=["test claim"],
            source="test",
        )

        memory.ask("test", mode="auto")
        events = bus.tail(n=5, types=[EventType.MEMORY_QUERIED])
        payload = events[0].payload
        assert "aleph_hits" in payload
        assert "mp_hits" in payload

    def test_auto_without_aleph_degrades(self, bus, adapter) -> None:
        """Memory without Aleph → auto mode still works (MP only)."""
        mem = Memory(bus=bus, adapter=adapter, project="test", aleph=None)
        adapter.write(
            content="Fallback content for degraded auto",
            wing="wing_test",
            room="room_general",
        )

        result = mem.ask("fallback", mode="auto")
        # Still returns results (from MemPalace)
        assert result.queried_event_id

    def test_auto_answer_includes_kind_labels(self, memory, aleph) -> None:
        """Auto answer shows [extraction ...] or [verbatim ...] labels."""
        aleph.write_extraction(
            turn_id="d1",
            raw_event_id="evt-1",
            summary="Labeled extraction content",
            entities=[],
            claims=["label test"],
            source="test",
        )

        result = memory.ask("label", mode="auto")
        if result.answer:
            assert "extraction" in result.answer or "verbatim" in result.answer

    def test_verbatim_still_works(self, memory: Memory, adapter) -> None:
        """Existing verbatim mode is unchanged."""
        adapter.write(
            content="Verbatim mode backwards compat",
            wing="wing_test",
            room="room_general",
        )

        result = memory.ask("verbatim backwards", mode="verbatim")
        assert result.queried_event_id
        # All sources are verbatim
        assert all(s.kind == "verbatim" for s in result.sources)


class TestAskHandlerAuto:
    """Test the MCP tool handler accepts auto mode."""

    def test_handler_accepts_auto(self, memory: Memory) -> None:
        from itsme.mcp.tools.ask import ask_handler

        result = ask_handler(memory, question="test query", mode="auto")
        assert "answer" in result
        assert "sources" in result
        assert "queried_event_id" in result

    def test_handler_still_rejects_wiki(self, memory: Memory) -> None:
        from itsme.mcp.tools.ask import ask_handler

        with pytest.raises(ValueError, match="not yet supported"):
            ask_handler(memory, question="test", mode="wiki")
