"""End-to-end tests — full pipeline with Obsidian vault integration.

Tests the complete flow:
  hook capture → intake → MemPalace + Aleph index → AlephRound → Obsidian vault
  → ask(mode=auto) triple-engine search
  → ask(mode=wiki) vault-only search
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.api import Aleph
from itsme.core.aleph.vault import AlephVault
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
def aleph() -> Iterator[Aleph]:
    a = Aleph(":memory:")
    yield a
    a.close()


@pytest.fixture
def vault(tmp_path: Path) -> AlephVault:
    """Create a minimal Aleph vault for testing."""
    vault_root = tmp_path / "aleph-vault"
    vault_root.mkdir()
    (vault_root / "dna.md").write_text("# Aleph DNA\n\nTest vault.\n")
    (vault_root / "index.md").write_text(
        "# Aleph Index\n\n"
        "<!-- Claude 维护 -->\n\n"
        "| 页面 | 类型 | Wing / 子类 | 摘要 | 更新日期 |\n"
        "|------|------|------------|------|--------|\n"
    )
    (vault_root / "log.md").write_text("# Aleph Log\n\n<!-- append-only -->\n\n")
    (vault_root / "wings").mkdir()
    (vault_root / "sources").mkdir()
    return AlephVault(vault_root)


def _emit_hook_turns(
    bus: EventBus,
    turns: list[tuple[str, str]],
    batch_id: str = "batch-vault",
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
# Full pipeline — intake → vault
# ============================================================


class TestVaultPipeline:
    """Intake → MemPalace + Aleph index → AlephRound → Obsidian vault."""

    def test_intake_creates_vault_pages(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """Kept turns flow through AlephRound and create vault wiki pages."""
        llm = _make_intake_and_round_llm(
            # Intake response: 2 kept turns
            intake_response=json.dumps([
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
            ]),
            # Round response: create 2 wiki pages
            round_response=json.dumps([
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
            ]),
        )

        events = _emit_hook_turns(bus, [
            ("user", "I decided to use Postgres for the user service"),
            ("user", "And Redis for the caching layer"),
        ])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            aleph=aleph,
            llm=llm,
            wing="wing_test",
            vault=vault,
        )
        results = processor.process_batch(events)

        # Intake results: both kept, both in MemPalace + Aleph
        assert len(results) == 2
        assert all(r.verdict == "keep" for r in results)
        assert all(r.drawer_id for r in results)

        # Vault pages created
        assert vault.find_page("postgres") is not None
        assert vault.find_page("redis") is not None

        # Index updated
        index = vault.read_index()
        assert any("postgres" in e.page_link for e in index)
        assert any("redis" in e.page_link for e in index)

        # Log updated
        log = (vault.root / "log.md").read_text()
        assert "[INGEST]" in log

    def test_intake_updates_existing_vault_page(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """AlephRound updates existing pages instead of creating dupes."""
        # Pre-create a page
        vault.write_page(
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
            intake_response=json.dumps([{
                "verdict": "keep",
                "summary": "Postgres now used for analytics too",
                "entities": [{"name": "Postgres", "type": "database"}],
                "claims": ["Postgres handles analytics workload"],
            }]),
            round_response=json.dumps([{
                "action": "update",
                "slug": "postgres",
                "add_related": ["[[analytics-pipeline]]"],
                "append_body": "> 新增用于分析管道\n",
                "history_entry": "- 2026-05-07 更新，新增分析用途",
            }]),
        )

        events = _emit_hook_turns(bus, [
            ("user", "We're also using Postgres for the analytics pipeline"),
        ])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            aleph=aleph,
            llm=llm,
            wing="wing_test",
            vault=vault,
        )
        processor.process_batch(events)

        # Page updated, not duplicated
        meta, body = vault.read_page("wings/technology/engineering/postgres.md")
        assert meta is not None
        assert "[[analytics-pipeline]]" in meta.related
        assert "分析管道" in body

    def test_skipped_turns_dont_trigger_round(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """When all turns are skipped, AlephRound is not called."""
        llm = StubProvider(
            response=json.dumps([
                {"verdict": "skip", "skip_reason": "greeting"},
            ]),
        )

        events = _emit_hook_turns(bus, [("user", "Hello!")])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            aleph=aleph,
            llm=llm,
            wing="wing_test",
            vault=vault,
        )
        results = processor.process_batch(events)

        assert results[0].verdict == "skip"
        assert results[0].drawer_id  # still in MemPalace
        assert len(vault.list_pages()) == 0  # no vault pages

    def test_wiki_promoted_event_emitted(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """wiki.promoted event is emitted when vault pages are created."""
        llm = _make_intake_and_round_llm(
            intake_response=json.dumps([{
                "verdict": "keep",
                "summary": "New tech decision",
                "entities": [],
                "claims": [],
            }]),
            round_response=json.dumps([{
                "action": "create",
                "slug": "new-decision",
                "domain": "technology",
                "subcategory": "ai",
                "type": "decision",
                "title": "New Decision",
                "summary": "A new technical decision",
            }]),
        )

        events = _emit_hook_turns(bus, [("user", "We decided on a new approach")])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            aleph=aleph,
            llm=llm,
            wing="wing_test",
            vault=vault,
        )
        processor.process_batch(events)

        # Check wiki.promoted event
        promoted = bus.tail(n=50, types=[EventType.WIKI_PROMOTED])
        assert len(promoted) == 1
        assert promoted[0].payload["pages_created"] == 1
        assert promoted[0].source == "worker:intake:vault-round"


# ============================================================
# ask(mode=wiki) — vault-only search
# ============================================================


class TestAskWiki:
    def test_ask_wiki_finds_vault_pages(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """ask(mode=wiki) searches vault pages."""
        # Create a vault page directly
        vault.write_page(
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
            bus=bus, adapter=adapter, project="test", aleph=aleph, vault=vault,
        )
        result = memory.ask("Postgres", mode="wiki")

        assert len(result.sources) >= 1
        assert all(s.kind == "wiki" for s in result.sources)
        assert any("Postgres" in s.content or "关系型" in s.content for s in result.sources)

    def test_ask_wiki_no_vault_empty(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """ask(mode=wiki) without vault returns empty, no error."""
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("anything", mode="wiki")
        assert result.sources == []


# ============================================================
# ask(mode=auto) — triple engine with vault
# ============================================================


class TestAskAutoWithVault:
    def test_auto_includes_vault_hits(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """ask(mode=auto) includes vault wiki pages in results."""
        # Vault page
        vault.write_page(
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
            bus=bus, adapter=adapter, project="test", aleph=aleph, vault=vault,
        )
        result = memory.ask("Redis caching", mode="auto")

        kinds = {s.kind for s in result.sources}
        # Should have both wiki and verbatim hits
        assert "wiki" in kinds
        assert "verbatim" in kinds

    def test_auto_vault_hit_ranked_first(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """Vault wiki hits appear before MemPalace raw hits."""
        vault.write_page(
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
            bus=bus, adapter=adapter, project="test", aleph=aleph, vault=vault,
        )
        result = memory.ask("Kubernetes", mode="auto")

        if len(result.sources) >= 2:
            # Wiki should come first
            assert result.sources[0].kind == "wiki"


# ============================================================
# Degradation — no vault
# ============================================================


class TestVaultDegradation:
    def test_intake_without_vault_still_works(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
    ) -> None:
        """IntakeProcessor without vault = v0.0.2 behavior, no crash."""
        llm = StubProvider(
            response=json.dumps([{
                "verdict": "keep",
                "summary": "Test",
                "entities": [],
                "claims": [],
            }]),
        )

        events = _emit_hook_turns(bus, [("user", "test content")])

        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            aleph=aleph,
            llm=llm,
            wing="wing_test",
            vault=None,  # no vault
        )
        results = processor.process_batch(events)

        assert len(results) == 1
        assert results[0].drawer_id  # MemPalace still written

    def test_degraded_llm_skips_vault_round(
        self,
        bus: EventBus,
        adapter: InMemoryMemPalaceAdapter,
        aleph: Aleph,
        vault: AlephVault,
    ) -> None:
        """Degraded LLM = no vault round, no crash."""
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            aleph=aleph,
            llm=StubProvider(),  # bare = degraded
            wing="wing_test",
            vault=vault,
        )

        events = _emit_hook_turns(bus, [("user", "important stuff")])
        results = processor.process_batch(events)

        assert results[0].drawer_id  # MemPalace written
        assert len(vault.list_pages()) == 0  # no vault writes (degraded)


# ============================================================
# Vault discovery
# ============================================================


class TestVaultDiscovery:
    def test_discover_vault_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """$ITSME_ALEPH_VAULT points to a vault."""
        from itsme.core.api import _discover_vault

        vault_root = tmp_path / "my-vault"
        vault_root.mkdir()
        (vault_root / "dna.md").write_text("# DNA\n")
        (vault_root / "wings").mkdir()
        (vault_root / "sources").mkdir()

        monkeypatch.setenv("ITSME_ALEPH_VAULT", str(vault_root))
        discovered = _discover_vault()
        assert discovered is not None
        assert discovered.root == vault_root.resolve()

    def test_discover_vault_missing_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """No vault at any candidate path → None."""
        from itsme.core.api import _discover_vault

        monkeypatch.setenv("ITSME_ALEPH_VAULT", "")
        # Override HOME so ~/Documents/Aleph/ doesn't accidentally exist
        monkeypatch.setenv("HOME", str(tmp_path))
        discovered = _discover_vault()
        assert discovered is None
