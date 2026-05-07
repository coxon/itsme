"""Tests for AlephRound — LLM-powered vault consolidation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from itsme.core.aleph.round import AlephRound, TurnContent, _parse_round_response
from itsme.core.aleph.vault import AlephVault
from itsme.core.llm import StubProvider


@pytest.fixture
def vault(tmp_path: Path) -> AlephVault:
    """Minimal Aleph vault."""
    (tmp_path / "dna.md").write_text("# Aleph DNA\n")
    (tmp_path / "index.md").write_text(
        "# Aleph Index\n\n"
        "<!-- Claude 维护，记录所有 wiki 页面。请勿手动大幅修改。 -->\n\n"
        "| 页面 | 类型 | Wing / 子类 | 摘要 | 更新日期 |\n"
        "|------|------|------------|------|--------|\n"
    )
    (tmp_path / "log.md").write_text("# Aleph Log\n\n<!-- append-only，不要修改已有行 -->\n\n")
    (tmp_path / "wings").mkdir()
    (tmp_path / "sources").mkdir()
    return AlephVault(tmp_path)


def _make_llm_response(operations: list[dict[str, object]]) -> str:
    return json.dumps(operations)


# ============================================================
# Create operations
# ============================================================


class TestRoundCreate:
    def test_creates_new_page(self, vault: AlephVault) -> None:
        """LLM says create → page exists in vault."""
        llm = StubProvider(
            response=_make_llm_response(
                [
                    {
                        "action": "create",
                        "slug": "postgres",
                        "domain": "technology",
                        "subcategory": "engineering",
                        "type": "concept",
                        "title": "Postgres",
                        "summary": "关系型数据库，选用于用户服务",
                        "body_section": "选择 Postgres 是因为并发写入性能优秀",
                        "related": [],
                    }
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process(
            [
                TurnContent(role="user", content="I decided to use Postgres for concurrent writes"),
            ]
        )

        assert result.pages_created == 1
        assert result.errors == []
        meta = vault.find_page("postgres")
        assert meta is not None
        assert meta.title == "Postgres"
        assert meta.domain == "technology"

    def test_creates_multiple_pages(self, vault: AlephVault) -> None:
        llm = StubProvider(
            response=_make_llm_response(
                [
                    {
                        "action": "create",
                        "slug": "redis",
                        "domain": "technology",
                        "subcategory": "engineering",
                        "type": "concept",
                        "title": "Redis",
                        "summary": "缓存层",
                    },
                    {
                        "action": "create",
                        "slug": "user-service",
                        "domain": "work",
                        "subcategory": "projects",
                        "type": "project",
                        "title": "User Service",
                        "summary": "用户服务微服务",
                    },
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process(
            [
                TurnContent(role="user", content="Redis for caching, User Service as microservice"),
            ]
        )

        assert result.pages_created == 2
        assert vault.find_page("redis") is not None
        assert vault.find_page("user-service") is not None

    def test_index_updated(self, vault: AlephVault) -> None:
        llm = StubProvider(
            response=_make_llm_response(
                [
                    {
                        "action": "create",
                        "slug": "test-page",
                        "domain": "technology",
                        "subcategory": "ai",
                        "type": "concept",
                        "title": "Test Page",
                        "summary": "A test",
                    },
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        rnd.process([TurnContent(role="user", content="test")])

        entries = vault.read_index()
        assert any("test-page" in e.page_link for e in entries)

    def test_log_appended(self, vault: AlephVault) -> None:
        llm = StubProvider(
            response=_make_llm_response(
                [
                    {
                        "action": "create",
                        "slug": "log-test",
                        "domain": "technology",
                        "subcategory": "ai",
                        "type": "concept",
                        "title": "Log Test",
                        "summary": "test",
                    },
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        rnd.process([TurnContent(role="user", content="test")])

        log = (vault.root / "log.md").read_text()
        assert "[INGEST]" in log
        assert "itsme:aleph-round" in log
        assert "新增 1 页" in log


# ============================================================
# Update operations
# ============================================================


class TestRoundUpdate:
    def test_updates_existing_page(self, vault: AlephVault) -> None:
        """LLM says update → existing page gains new content."""
        # Create a page first
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
                "tags": ["wing/technology", "type/concept"],
                "last_verified": "2026-05-01",
            },
            body="# Postgres\n\n## History\n- 2026-05-01 创建\n",
        )

        llm = StubProvider(
            response=_make_llm_response(
                [
                    {
                        "action": "update",
                        "slug": "postgres",
                        "add_related": ["[[user-service]]"],
                        "append_body": "> 新增：用于用户服务的主数据库\n",
                        "history_entry": "- 2026-05-07 更新，来源: itsme intake",
                    }
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process(
            [
                TurnContent(role="user", content="Postgres is our main DB for user service"),
            ]
        )

        assert result.pages_updated == 1
        meta, body = vault.read_page("wings/technology/engineering/postgres.md")
        assert meta is not None
        assert "[[user-service]]" in meta.related
        assert "用户服务的主数据库" in body
        assert "2026-05-07 更新" in body

    def test_update_nonexistent_records_error(self, vault: AlephVault) -> None:
        """Update of nonexistent page → error in result, no crash."""
        llm = StubProvider(
            response=_make_llm_response(
                [
                    {"action": "update", "slug": "nonexistent", "append_body": "x"},
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process([TurnContent(role="user", content="test")])

        assert result.pages_updated == 0
        assert len(result.errors) >= 1


# ============================================================
# Mixed operations
# ============================================================


class TestRoundMixed:
    def test_create_and_update_in_one_round(self, vault: AlephVault) -> None:
        # Pre-existing page
        vault.write_page(
            slug="redis",
            domain="technology",
            subcategory="engineering",
            frontmatter={
                "title": "Redis",
                "type": "concept",
                "domain": "technology",
                "subcategory": "engineering",
                "summary": "缓存",
                "sources": [],
                "related": [],
                "tags": [],
                "last_verified": "2026-05-01",
            },
            body="# Redis\n\n## History\n- 2026-05-01 创建\n",
        )

        llm = StubProvider(
            response=_make_llm_response(
                [
                    {
                        "action": "create",
                        "slug": "session-store",
                        "domain": "technology",
                        "subcategory": "engineering",
                        "type": "project",
                        "title": "Session Store",
                        "summary": "Redis-backed session storage",
                    },
                    {
                        "action": "update",
                        "slug": "redis",
                        "add_related": ["[[session-store]]"],
                        "history_entry": "- 2026-05-07 关联 session store",
                    },
                ]
            )
        )

        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process(
            [
                TurnContent(role="user", content="We use Redis for session storage"),
            ]
        )

        assert result.pages_created == 1
        assert result.pages_updated == 1
        assert vault.find_page("session-store") is not None
        meta = vault.find_page("redis")
        assert meta is not None
        assert "[[session-store]]" in meta.related


# ============================================================
# LLM edge cases
# ============================================================


class TestRoundLLMEdgeCases:
    def test_empty_turns(self, vault: AlephVault) -> None:
        llm = StubProvider(response="[]")
        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process([])
        assert result.pages_created == 0

    def test_llm_returns_empty_array(self, vault: AlephVault) -> None:
        """LLM decides nothing is wiki-worthy."""
        llm = StubProvider(response="[]")
        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process(
            [
                TurnContent(role="user", content="Hello, how are you?"),
            ]
        )
        assert result.pages_created == 0
        assert result.pages_skipped == 1

    def test_llm_returns_garbage(self, vault: AlephVault) -> None:
        llm = StubProvider(response="this is not json at all")
        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process([TurnContent(role="user", content="test")])
        assert result.pages_created == 0

    def test_llm_returns_markdown_fenced(self, vault: AlephVault) -> None:
        inner = _make_llm_response(
            [
                {
                    "action": "create",
                    "slug": "fenced",
                    "domain": "technology",
                    "subcategory": "ai",
                    "type": "concept",
                    "title": "Fenced",
                    "summary": "test",
                }
            ]
        )
        llm = StubProvider(response=f"```json\n{inner}\n```")
        rnd = AlephRound(vault=vault, llm=llm)
        result = rnd.process([TurnContent(role="user", content="test")])
        assert result.pages_created == 1

    def test_llm_unavailable_degrades(self, vault: AlephVault) -> None:
        """Bare StubProvider = degraded mode, no crash."""
        rnd = AlephRound(vault=vault, llm=StubProvider())
        result = rnd.process([TurnContent(role="user", content="important stuff")])
        # Degraded: empty response → no operations
        assert result.pages_created == 0


# ============================================================
# Response parsing
# ============================================================


class TestParseRoundResponse:
    def test_valid_create(self) -> None:
        ops = _parse_round_response(
            json.dumps(
                [
                    {
                        "action": "create",
                        "slug": "test",
                        "domain": "technology",
                        "subcategory": "ai",
                        "type": "concept",
                        "title": "Test",
                    }
                ]
            )
        )
        assert len(ops) == 1
        assert ops[0]["action"] == "create"

    def test_valid_update(self) -> None:
        ops = _parse_round_response(
            json.dumps(
                [
                    {"action": "update", "slug": "test", "append_body": "new stuff"},
                ]
            )
        )
        assert len(ops) == 1

    def test_malformed_create_rejected(self) -> None:
        """Create without required fields is skipped."""
        ops = _parse_round_response(
            json.dumps(
                [
                    {"action": "create", "slug": "test"},  # missing domain, type, etc
                ]
            )
        )
        assert len(ops) == 0

    def test_non_json(self) -> None:
        assert _parse_round_response("not json") == []

    def test_non_array(self) -> None:
        assert _parse_round_response('{"action": "create"}') == []

    def test_markdown_fences_stripped(self) -> None:
        ops = _parse_round_response('```json\n[{"action": "update", "slug": "x"}]\n```')
        assert len(ops) == 1

    def test_empty_slug_rejected(self) -> None:
        """Create with empty slug is rejected."""
        ops = _parse_round_response(
            json.dumps(
                [
                    {
                        "action": "create",
                        "slug": "",
                        "domain": "technology",
                        "subcategory": "ai",
                        "type": "concept",
                        "title": "Empty Slug",
                    }
                ]
            )
        )
        assert len(ops) == 0

    def test_whitespace_only_slug_rejected(self) -> None:
        ops = _parse_round_response(
            json.dumps([{"action": "update", "slug": "   "}])
        )
        assert len(ops) == 0

    def test_non_list_related_rejected(self) -> None:
        """related field as string instead of list is rejected."""
        ops = _parse_round_response(
            json.dumps(
                [
                    {
                        "action": "create",
                        "slug": "test",
                        "domain": "technology",
                        "subcategory": "ai",
                        "type": "concept",
                        "title": "Test",
                        "related": "not-a-list",
                    }
                ]
            )
        )
        assert len(ops) == 0
