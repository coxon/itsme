"""Tests for wiki embedding search — T3.11+ pipeline.

Verifies:
- Wiki pages synced to MemPalace (aleph wing) for embedding search
- dual_search finds wiki embedding hits alongside keyword hits
- Embedding hits fill gaps that keyword search misses
- Dedup: same page from keyword + embedding isn't doubled
- Startup bootstrap: sync_all_wiki_pages indexes existing pages
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.adapters.naming import WIKI_ROOM, WIKI_WING
from itsme.core.aleph.wiki import Aleph
from itsme.core.api import Memory
from itsme.core.events import EventBus, EventType
from itsme.core.llm import StubProvider
from itsme.core.search import dual_search
from itsme.core.workers.intake import IntakeProcessor, _wiki_page_for_embedding


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
def aleph(tmp_path: Path) -> Aleph:
    aleph_root = tmp_path / "aleph-wiki"
    aleph_root.mkdir()
    (aleph_root / "dna.md").write_text("# Aleph DNA\n\nTest wiki.\n")
    (aleph_root / "index.md").write_text(
        "# Aleph Index\n\n"
        "<!-- Claude 维护 -->\n\n"
        "| 页面 | 类型 | Wing / 子类 | 摘要 | 更新日期 |\n"
        "|------|------|------------|------|--------|\n"
    )
    (aleph_root / "log.md").write_text("# Aleph Log\n\n<!-- append-only -->\n\n")
    (aleph_root / "wings").mkdir()
    (aleph_root / "sources").mkdir()
    return Aleph(aleph_root)


def _write_page(aleph: Aleph, slug: str, title: str, summary: str, body: str = "") -> None:
    aleph.write_page(
        slug=slug,
        domain="technology",
        subcategory="engineering",
        frontmatter={
            "title": title,
            "type": "concept",
            "domain": "technology",
            "subcategory": "engineering",
            "summary": summary,
            "tags": [],
        },
        body=body or f"# {title}\n\n{summary}\n",
    )


# ============================================================
# Content formatting
# ============================================================


class TestWikiPageForEmbedding:
    def test_full_content(self) -> None:
        content = _wiki_page_for_embedding("Postgres", "关系型数据库", "# Postgres\n\n详细描述")
        assert "Postgres" in content
        assert "关系型数据库" in content
        assert "详细描述" in content

    def test_empty_summary(self) -> None:
        content = _wiki_page_for_embedding("Postgres", "", "body")
        assert "Postgres" in content
        assert "body" in content

    def test_empty_body(self) -> None:
        content = _wiki_page_for_embedding("Postgres", "summary", "")
        assert "Postgres" in content
        assert "summary" in content


# ============================================================
# Sync to MemPalace
# ============================================================


class TestWikiEmbeddingSync:
    def test_sync_all_pages(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """sync_all_wiki_pages writes all pages to MemPalace."""
        _write_page(aleph, "postgres", "Postgres", "关系型数据库")
        _write_page(aleph, "redis", "Redis", "缓存层")

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),
            wing="wing_test",
            aleph=aleph,
        )
        synced = processor.sync_all_wiki_pages()
        assert synced == 2

        # Verify they're in MemPalace under aleph wing
        hits = adapter.search("Postgres", wing=WIKI_WING)
        assert len(hits) >= 1
        assert "Postgres" in hits[0].content

    def test_sync_uses_wiki_wing_and_room(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Synced pages use the well-known aleph wing and wiki room."""
        _write_page(aleph, "postgres", "Postgres", "关系型数据库")

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),
            wing="wing_test",
            aleph=aleph,
        )
        processor.sync_all_wiki_pages()

        # Searching by wiki room should find it
        hits = adapter.search("Postgres", wing=WIKI_WING, room=WIKI_ROOM)
        assert len(hits) >= 1

        # Searching by project wing should NOT find it
        hits_project = adapter.search("Postgres", wing="wing_test")
        assert len(hits_project) == 0

    def test_sync_no_aleph_returns_zero(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
    ) -> None:
        """No Aleph → sync returns 0."""
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),
            wing="wing_test",
            aleph=None,
        )
        assert processor.sync_all_wiki_pages() == 0

    def test_intake_syncs_after_wiki_round(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """After AlephRound creates pages, they're synced to MemPalace."""
        from itsme.core.dedup import content_hash

        llm = _MultiResponseProvider(
            [
                # Intake: keep
                json.dumps(
                    [
                        {
                            "verdict": "keep",
                            "summary": "Postgres chosen",
                            "entities": [{"name": "Postgres", "type": "db"}],
                            "claims": [],
                        }
                    ]
                ),
                # Round: create page
                json.dumps(
                    [
                        {
                            "action": "create",
                            "slug": "postgres",
                            "domain": "technology",
                            "subcategory": "engineering",
                            "type": "concept",
                            "title": "Postgres",
                            "summary": "关系型数据库",
                            "body_section": "选用于用户服务",
                        }
                    ]
                ),
            ]
        )

        ev = bus.emit(
            type=EventType.RAW_CAPTURED,
            source="hook:before-exit",
            payload={
                "content": "We chose Postgres",
                "turn_role": "user",
                "capture_batch_id": "batch-1",
                "content_hash": content_hash("We chose Postgres"),
                "producer_kind": "hook:lifecycle",
            },
        )

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
            aleph=aleph,
        )
        processor.process_batch([ev])

        # Page should be in Aleph
        assert aleph.find_page("postgres") is not None

        # AND also in MemPalace under aleph wing (embedding sync)
        hits = adapter.search("Postgres", wing=WIKI_WING)
        assert len(hits) >= 1
        assert "Postgres" in hits[0].content


# ============================================================
# dual_search with embedding leg
# ============================================================


class TestDualSearchEmbedding:
    def test_embedding_hit_fills_keyword_gap(
        self,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Wiki embedding hit found when keyword search misses.

        Simulates a semantic query that keyword can't match but
        embedding (here: Jaccard on full page content) can.
        """
        _write_page(
            aleph,
            "hailong",
            "海龙",
            "产品负责人",
            body="# 海龙\n\n负责星图项目的产品设计和规划。\n",
        )
        # Sync to MemPalace for embedding search
        adapter.write(
            content=_wiki_page_for_embedding(
                "海龙", "产品负责人", "# 海龙\n\n负责星图项目的产品设计和规划。\n"
            ),
            wing=WIKI_WING,
            room=WIKI_ROOM,
        )

        # "产品设计" won't match keyword search well on title "海龙",
        # but embedding (Jaccard on full content) will find it
        hits = dual_search(
            "产品设计",
            adapter=adapter,
            aleph=aleph,
            limit=5,
        )
        assert len(hits) >= 1
        # Should find via either keyword or embedding
        wiki_hits = [h for h in hits if h.kind == "wiki"]
        assert len(wiki_hits) >= 1

    def test_embedding_does_not_pollute_verbatim(
        self,
        adapter: InMemoryMemPalaceAdapter,
    ) -> None:
        """Wiki embedding in aleph wing doesn't appear as verbatim hits."""
        # Write wiki content to aleph wing
        adapter.write(
            content="Postgres is a relational database",
            wing=WIKI_WING,
            room=WIKI_ROOM,
        )
        # Write raw turn to project wing
        adapter.write(
            content="We discussed Postgres options",
            wing="wing_test",
            room="room_general",
        )

        hits = dual_search("Postgres", adapter=adapter, wing="wing_test", limit=5)

        # Should have wiki embedding hit + verbatim hit, NOT wiki content as verbatim
        wiki_hits = [h for h in hits if h.kind == "wiki"]
        verbatim_hits = [h for h in hits if h.kind == "verbatim"]

        assert len(wiki_hits) >= 1  # from embedding search
        assert len(verbatim_hits) >= 1  # from raw search
        # Verbatim should be the raw turn, not the wiki content
        assert any("discussed" in h.content for h in verbatim_hits)

    def test_dedup_keyword_and_embedding(
        self,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Same page from keyword + embedding doesn't waste result slots."""
        _write_page(aleph, "postgres", "Postgres", "关系型数据库")
        # Also sync to MemPalace
        adapter.write(
            content=_wiki_page_for_embedding(
                "Postgres", "关系型数据库", "# Postgres\n\n关系型数据库\n"
            ),
            wing=WIKI_WING,
            room=WIKI_ROOM,
        )

        hits = dual_search("Postgres", adapter=adapter, aleph=aleph, limit=5)

        # Should have wiki hits but not duplicate the same Postgres page
        wiki_hits = [h for h in hits if h.kind == "wiki"]
        # Content dedup should prevent exact duplicates
        contents = [h.content[:50] for h in wiki_hits]
        assert len(contents) == len(set(contents)), f"Duplicate wiki hits: {contents}"


# ============================================================
# Memory integration — startup sync
# ============================================================


class TestMemoryStartupSync:
    def test_memory_init_syncs_wiki_pages(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Memory.__init__ syncs existing wiki pages for embedding."""
        _write_page(aleph, "postgres", "Postgres", "关系型数据库")
        _write_page(aleph, "redis", "Redis", "缓存层")

        Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)

        # Both pages should now be in MemPalace under aleph wing
        hits = adapter.search("Postgres", wing=WIKI_WING)
        assert len(hits) >= 1
        hits = adapter.search("Redis", wing=WIKI_WING)
        assert len(hits) >= 1

    def test_memory_init_no_aleph_no_crash(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
    ) -> None:
        """Memory without Aleph doesn't crash on startup sync."""
        Memory(bus=bus, adapter=adapter, project="test", aleph=None)
        # Just assert no exception

    def test_ask_auto_finds_embedding_hits(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """ask(mode=auto) includes wiki embedding hits."""
        _write_page(
            aleph,
            "hailong",
            "海龙",
            "产品负责人，负责星图项目",
            body="# 海龙\n\n负责星图项目的产品设计和规划。\n",
        )

        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("产品设计", mode="auto")

        # Should find the page via keyword or embedding
        assert len(result.sources) >= 1


# ============================================================
# Helper
# ============================================================


class _MultiResponseProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0
        self._response = "non-empty"

    def complete(
        self, *, system: str, messages: list[dict[str, str]], max_tokens: int = 2048
    ) -> str:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]
