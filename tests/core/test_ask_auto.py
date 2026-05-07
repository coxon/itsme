"""Tests for ask(mode='auto') — Memory integration.

Verifies that Memory.ask() routes through dual_search correctly
and emits the right events.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.vault import AlephVault
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
def vault(tmp_path: Path) -> AlephVault:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "dna.md").write_text("# DNA\n")
    (vault_root / "wings").mkdir()
    (vault_root / "sources").mkdir()
    return AlephVault(vault_root)


@pytest.fixture
def memory(bus: EventBus, adapter: InMemoryMemPalaceAdapter) -> Memory:
    return Memory(bus=bus, adapter=adapter, project="test")


@pytest.fixture
def memory_with_vault(
    bus: EventBus, adapter: InMemoryMemPalaceAdapter, vault: AlephVault
) -> Memory:
    return Memory(bus=bus, adapter=adapter, project="test", vault=vault)


class TestAskAuto:
    def test_auto_mode_accepted(self, memory: Memory) -> None:
        """mode='auto' no longer raises."""
        result = memory.ask("anything", mode="auto")
        assert result.queried_event_id

    def test_auto_returns_mempalace_hits(
        self, memory: Memory, adapter: InMemoryMemPalaceAdapter
    ) -> None:
        """MemPalace raw hits show up as verbatim in auto mode."""
        adapter.write(
            content="Redis is used for session caching",
            wing="wing_test",
            room="room_general",
        )

        result = memory.ask("Redis caching", mode="auto")
        mp_sources = [s for s in result.sources if s.kind == "verbatim"]
        assert len(mp_sources) >= 1

    def test_auto_returns_vault_hits(self, memory_with_vault: Memory, vault: AlephVault) -> None:
        """Vault wiki hits show up in auto mode."""
        vault.write_page(
            slug="postgres",
            domain="technology",
            subcategory="engineering",
            frontmatter={
                "title": "Postgres",
                "type": "concept",
                "domain": "technology",
                "subcategory": "engineering",
                "summary": "Relational database for concurrent writes",
                "tags": [],
            },
            body="# Postgres\n\nChosen for concurrent writes.\n",
        )

        result = memory_with_vault.ask("Postgres", mode="auto")
        wiki_sources = [s for s in result.sources if s.kind == "wiki"]
        assert len(wiki_sources) >= 1
        assert any("Postgres" in s.content for s in wiki_sources)

    def test_auto_emits_queried_event_with_mode(self, memory: Memory, bus: EventBus) -> None:
        """memory.queried event carries mode='auto'."""
        memory.ask("anything", mode="auto")
        events = bus.tail(n=5, types=[EventType.MEMORY_QUERIED])
        assert len(events) >= 1
        assert events[0].payload["mode"] == "auto"

    def test_auto_event_has_hit_breakdown(self, memory: Memory, bus: EventBus) -> None:
        """Event payload includes wiki_hits / mp_hits counts."""
        memory.ask("test", mode="auto")
        events = bus.tail(n=5, types=[EventType.MEMORY_QUERIED])
        payload = events[0].payload
        assert "wiki_hits" in payload
        assert "mp_hits" in payload

    def test_auto_without_vault_works(
        self, bus: EventBus, adapter: InMemoryMemPalaceAdapter
    ) -> None:
        """Memory without vault → auto mode still works (MP only)."""
        mem = Memory(bus=bus, adapter=adapter, project="test")
        adapter.write(
            content="Fallback content for auto",
            wing="wing_test",
            room="room_general",
        )

        result = mem.ask("fallback", mode="auto")
        assert result.queried_event_id

    def test_auto_answer_includes_kind_labels(
        self, memory: Memory, adapter: InMemoryMemPalaceAdapter
    ) -> None:
        """Auto answer shows [verbatim ...] or [wiki ...] labels."""
        adapter.write(
            content="Labeled content for testing",
            wing="wing_test",
            room="room_general",
        )

        result = memory.ask("labeled", mode="auto")
        if result.answer:
            assert "verbatim" in result.answer or "wiki" in result.answer

    def test_verbatim_still_works(self, memory: Memory, adapter: InMemoryMemPalaceAdapter) -> None:
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
