"""End-to-end tests — full pipeline with Aleph wiki integration.

Tests the complete flow:
  hook capture → intake → MemPalace → AlephRound → wiki pages
  → ask(mode=auto) dual-engine search
  → ask(mode=wiki) wiki-only search
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.wiki import Aleph
from itsme.core.api import Memory
from itsme.core.events import EventBus, EventType
from itsme.core.llm import StubProvider
from itsme.core.workers.intake import IntakeProcessor


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
    """Create a minimal Aleph wiki for testing."""
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


def _emit_hook_turns(
    bus: EventBus,
    turns: list[tuple[str, str]],
    batch_id: str = "batch-wiki",
) -> list:
    """Simulate hook-captured per-turn events."""
    from itsme.core.dedup import content_hash

    events = []
    for role, text in turns:
        ev = bus.emit(
            type=EventType.RAW_CAPTURED,
            source="hook:before-exit",
            payload={
                "content": text,
                "turn_role": role,
                "capture_batch_id": batch_id,
                "content_hash": content_hash(text),
                "producer_kind": "hook:lifecycle",
            },
        )
        events.append(ev)
    return events


def _make_intake_and_round_llm(
    intake_response: str,
    round_response: str,
) -> StubProvider:
    """Build a StubProvider that returns different responses per call.

    First call → intake extraction, second call → round wiki ops.
    """
    return _MultiResponseProvider([intake_response, round_response])


class _MultiResponseProvider:
    """Returns different responses for successive calls."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0
        self._response = "non-empty"  # marks as non-degraded

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
    ) -> str:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]


# ============================================================
# Full pipeline — intake → wiki
# ============================================================


class TestVaultPipeline:
    """Intake → MemPalace → AlephRound → wiki pages."""

    def test_intake_creates_wiki_pages(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Kept turns flow through AlephRound and create wiki pages."""
        llm = _make_intake_and_round_llm(
            # Intake response: 2 kept turns
            intake_response=json.dumps(
                [
                    {
                        "verdict": "keep",
                        "summary": "User chose Postgres for the user service",
                        "entities": [{"name": "Postgres", "type": "database"}],
                        "claims": ["Postgres chosen for user service"],
                    },
                    {
                        "verdict": "keep",
                        "summary": "Redis selected for caching layer",
                        "entities": [{"name": "Redis", "type": "database"}],
                        "claims": ["Redis for caching"],
                    },
                ]
            ),
            # Round response: create 2 wiki pages
            round_response=json.dumps(
                [
                    {
                        "action": "create",
                        "slug": "postgres",
                        "domain": "technology",
                        "subcategory": "engineering",
                        "type": "concept",
                        "title": "Postgres",
                        "summary": "关系型数据库，选用于用户服务",
                        "body_section": "因并发写入需求选择",
                    },
                    {
                        "action": "create",
                        "slug": "redis",
                        "domain": "technology",
                        "subcategory": "engineering",
                        "type": "concept",
                        "title": "Redis",
                        "summary": "缓存层",
                    },
                ]
            ),
        )

        events = _emit_hook_turns(
            bus,
            [
                ("user", "I decided to use Postgres for the user service"),
                ("user", "And Redis for the caching layer"),
            ],
        )

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
            aleph=aleph,
        )
        results = processor.process_batch(events)

        # Intake results: both kept, both in MemPalace
        assert len(results) == 2
        assert all(r.verdict == "keep" for r in results)
        assert all(r.drawer_id for r in results)

        # Vault pages created
        assert aleph.find_page("postgres") is not None
        assert aleph.find_page("redis") is not None

        # Index updated
        index = aleph.read_index()
        assert any("postgres" in e.page_link for e in index)
        assert any("redis" in e.page_link for e in index)

        # Log updated
        log = (aleph.root / "log.md").read_text()
        assert "[INGEST]" in log

    def test_intake_updates_existing_wiki_page(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """AlephRound updates existing pages instead of creating dupes."""
        # Pre-create a page
        aleph.write_page(
            slug="postgres",
            domain="technology",
            subcategory="engineering",
            frontmatter={
                "title": "Postgres",
                "type": "concept",
                "domain": "technology",
                "subcategory": "engineering",
                "summary": "关系型数据库",
                "sources": [],
                "related": [],
                "tags": ["wing/technology"],
                "last_verified": "2026-05-01",
            },
            body="# Postgres\n\n## History\n- 2026-05-01 创建\n",
        )

        llm = _make_intake_and_round_llm(
            intake_response=json.dumps(
                [
                    {
                        "verdict": "keep",
                        "summary": "Postgres now used for analytics too",
                        "entities": [{"name": "Postgres", "type": "database"}],
                        "claims": ["Postgres handles analytics workload"],
                    }
                ]
            ),
            round_response=json.dumps(
                [
                    {
                        "action": "update",
                        "slug": "postgres",
                        "add_related": ["[[analytics-pipeline]]"],
                        "append_body": "> 新增用于分析管道\n",
                        "history_entry": "- 2026-05-07 更新，新增分析用途",
                    }
                ]
            ),
        )

        events = _emit_hook_turns(
            bus,
            [
                ("user", "We're also using Postgres for the analytics pipeline"),
            ],
        )

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
            aleph=aleph,
        )
        processor.process_batch(events)

        # Page updated, not duplicated
        meta, body = aleph.read_page("wings/technology/engineering/postgres.md")
        assert meta is not None
        assert "[[analytics-pipeline]]" in meta.related
        assert "分析管道" in body

    def test_skipped_turns_dont_trigger_round(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """When all turns are skipped, AlephRound is not called."""
        llm = StubProvider(
            response=json.dumps(
                [
                    {"verdict": "skip", "skip_reason": "greeting"},
                ]
            ),
        )

        events = _emit_hook_turns(bus, [("user", "Hello!")])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
            aleph=aleph,
        )
        results = processor.process_batch(events)

        assert results[0].verdict == "skip"
        assert results[0].drawer_id  # still in MemPalace
        assert len(aleph.list_pages()) == 0  # no wiki pages

    def test_wiki_promoted_event_emitted(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """wiki.promoted event is emitted when wiki pages are created."""
        llm = _make_intake_and_round_llm(
            intake_response=json.dumps(
                [
                    {
                        "verdict": "keep",
                        "summary": "New tech decision",
                        "entities": [],
                        "claims": [],
                    }
                ]
            ),
            round_response=json.dumps(
                [
                    {
                        "action": "create",
                        "slug": "new-decision",
                        "domain": "technology",
                        "subcategory": "ai",
                        "type": "decision",
                        "title": "New Decision",
                        "summary": "A new technical decision",
                    }
                ]
            ),
        )

        events = _emit_hook_turns(bus, [("user", "We decided on a new approach")])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
            aleph=aleph,
        )
        processor.process_batch(events)

        # Check wiki.promoted event
        promoted = bus.tail(n=50, types=[EventType.WIKI_PROMOTED])
        assert len(promoted) == 1
        assert promoted[0].payload["pages_created"] == 1
        assert promoted[0].source == "worker:intake:wiki-round"


# ============================================================
# ask(mode=wiki) — wiki-only search
# ============================================================


class TestAskWiki:
    def test_ask_wiki_finds_pages(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """ask(mode=wiki) searches wiki pages."""
        # Create a wiki page directly
        aleph.write_page(
            slug="postgres",
            domain="technology",
            subcategory="engineering",
            frontmatter={
                "title": "Postgres",
                "type": "concept",
                "domain": "technology",
                "subcategory": "engineering",
                "summary": "关系型数据库，用于用户服务",
                "aliases": ["PostgreSQL"],
                "tags": ["wing/technology"],
            },
            body="# Postgres\n\n并发写入性能优秀。\n",
        )

        memory = Memory(
            bus=bus,
            adapter=adapter,
            project="test",
            aleph=aleph,
        )
        result = memory.ask("Postgres", mode="wiki")

        assert len(result.sources) >= 1
        assert all(s.kind == "wiki" for s in result.sources)
        assert any("Postgres" in s.content or "关系型" in s.content for s in result.sources)

    def test_ask_wiki_no_aleph_empty(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
    ) -> None:
        """ask(mode=wiki) without Aleph returns empty, no error."""
        memory = Memory(bus=bus, adapter=adapter, project="test")
        result = memory.ask("anything", mode="wiki")
        assert result.sources == []


# ============================================================
# ask(mode=auto) — dual engine with Aleph
# ============================================================


class TestAskAutoWithVault:
    def test_auto_includes_wiki_hits(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """ask(mode=auto) includes wiki pages in results."""
        # Vault page
        aleph.write_page(
            slug="redis",
            domain="technology",
            subcategory="engineering",
            frontmatter={
                "title": "Redis",
                "type": "concept",
                "domain": "technology",
                "subcategory": "engineering",
                "summary": "In-memory cache for session storage",
                "tags": [],
            },
            body="# Redis\n\nUsed for caching.\n",
        )

        # MemPalace raw hit
        adapter.write(
            content="Redis is great for caching and pub/sub",
            wing="wing_test",
            room="room_general",
        )

        memory = Memory(
            bus=bus,
            adapter=adapter,
            project="test",
            aleph=aleph,
        )
        result = memory.ask("Redis caching", mode="auto")

        kinds = {s.kind for s in result.sources}
        # Should have both wiki and verbatim hits
        assert "wiki" in kinds
        assert "verbatim" in kinds

    def test_auto_wiki_hit_ranked_first(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Wiki hits appear before MemPalace raw hits."""
        aleph.write_page(
            slug="kubernetes",
            domain="technology",
            subcategory="engineering",
            frontmatter={
                "title": "Kubernetes",
                "type": "concept",
                "domain": "technology",
                "subcategory": "engineering",
                "summary": "Container orchestration platform",
                "tags": [],
            },
            body="# Kubernetes\n\nK8s for production.\n",
        )

        adapter.write(
            content="Kubernetes deployment was tricky",
            wing="wing_test",
            room="room_general",
        )

        memory = Memory(
            bus=bus,
            adapter=adapter,
            project="test",
            aleph=aleph,
        )
        result = memory.ask("Kubernetes", mode="auto")

        if len(result.sources) >= 2:
            # Wiki should come first
            assert result.sources[0].kind == "wiki"


# ============================================================
# Degradation — no Aleph
# ============================================================


class TestVaultDegradation:
    def test_intake_without_aleph_still_works(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
    ) -> None:
        """IntakeProcessor without Aleph = basic behavior, no crash."""
        llm = StubProvider(
            response=json.dumps(
                [
                    {
                        "verdict": "keep",
                        "summary": "Test",
                        "entities": [],
                        "claims": [],
                    }
                ]
            ),
        )

        events = _emit_hook_turns(bus, [("user", "test content")])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
            aleph=None,  # no Aleph
        )
        results = processor.process_batch(events)

        assert len(results) == 1
        assert results[0].drawer_id  # MemPalace still written

    def test_degraded_llm_skips_wiki_round(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """Degraded LLM = no Aleph round, no crash."""
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),  # bare = degraded
            wing="wing_test",
            aleph=aleph,
        )

        events = _emit_hook_turns(bus, [("user", "important stuff")])
        results = processor.process_batch(events)

        assert results[0].drawer_id  # MemPalace written
        assert len(aleph.list_pages()) == 0  # no Aleph writes (degraded)


# ============================================================
# Vault discovery
# ============================================================


class TestVaultDiscovery:
    def test_discover_aleph_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """$ITSME_ALEPH_ROOT points to a wiki."""
        from itsme.core.api import _discover_aleph

        aleph_root = tmp_path / "my-wiki"
        aleph_root.mkdir()
        (aleph_root / "dna.md").write_text("# DNA\n")
        (aleph_root / "wings").mkdir()
        (aleph_root / "sources").mkdir()

        monkeypatch.setenv("ITSME_ALEPH_ROOT", str(aleph_root))
        discovered = _discover_aleph()
        assert discovered is not None
        assert discovered.root == aleph_root.resolve()

    def test_discover_aleph_missing_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """No wiki at any candidate path → None."""
        from itsme.core.api import _discover_aleph

        monkeypatch.setenv("ITSME_ALEPH_ROOT", "")
        # Override HOME so ~/Documents/Aleph/ doesn't accidentally exist
        monkeypatch.setenv("HOME", str(tmp_path))
        discovered = _discover_aleph()
        assert discovered is None
