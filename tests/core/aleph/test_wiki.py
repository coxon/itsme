"""Tests for Aleph — Obsidian wiki adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.aleph.wiki import Aleph, IndexEntry


@pytest.fixture
def aleph(tmp_path: Path) -> Aleph:
    """Create a minimal Aleph wiki for testing."""
    # dna.md (required marker)
    (tmp_path / "dna.md").write_text("# Aleph DNA\n\nTest wiki.\n", encoding="utf-8")
    # index.md
    (tmp_path / "index.md").write_text(
        "# Aleph Index\n\n"
        "<!-- Claude 维护，记录所有 wiki 页面。请勿手动大幅修改。 -->\n\n"
        "| 页面 | 类型 | Wing / 子类 | 摘要 | 更新日期 |\n"
        "|------|------|------------|------|--------|\n",
        encoding="utf-8",
    )
    # log.md
    (tmp_path / "log.md").write_text(
        "# Aleph Log\n\n<!-- append-only，不要修改已有行 -->\n\n",
        encoding="utf-8",
    )
    # wings directory
    (tmp_path / "wings").mkdir()
    # sources directory
    (tmp_path / "sources").mkdir()
    return Aleph(tmp_path)


def _write_sample_page(aleph: Aleph, slug: str = "test-concept") -> Path:
    """Write a sample page and return its relative path."""
    return aleph.write_page(
        slug=slug,
        domain="technology",
        subcategory="ai",
        frontmatter={
            "title": "Test Concept",
            "type": "concept",
            "domain": "technology",
            "subcategory": "ai",
            "aliases": ["TC", "test"],
            "summary": "A test concept for unit testing",
            "sources": [],
            "related": [],
            "tags": ["wing/technology", "type/concept"],
            "last_verified": "2026-05-07",
        },
        body=(
            "# Test Concept\n\n"
            "> [!info] 核心摘要\n"
            "> This is a test concept used for unit testing.\n\n"
            "## History\n"
            "- 2026-05-07 创建\n"
        ),
    )


# ============================================================
# Init and structure
# ============================================================


class TestVaultInit:
    def test_valid_init(self, aleph: Aleph) -> None:
        assert aleph.root.exists()

    def test_missing_dna_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="dna.md"):
            Aleph(tmp_path)

    def test_root_property(self, aleph: Aleph) -> None:
        assert aleph.root.is_dir()


# ============================================================
# Read operations
# ============================================================


class TestReadOperations:
    def test_list_pages_empty(self, aleph: Aleph) -> None:
        assert aleph.list_pages() == []

    def test_list_pages_finds_pages(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        pages = aleph.list_pages()
        assert len(pages) == 1
        assert pages[0].title == "Test Concept"
        assert pages[0].type == "concept"
        assert pages[0].domain == "technology"

    def test_read_page(self, aleph: Aleph) -> None:
        rel = _write_sample_page(aleph)
        meta, body = aleph.read_page(rel)
        assert meta is not None
        assert meta.title == "Test Concept"
        assert "unit testing" in body

    def test_read_page_not_found(self, aleph: Aleph) -> None:
        meta, body = aleph.read_page("wings/nonexistent.md")
        assert meta is None
        assert body == ""

    def test_find_page_by_slug(self, aleph: Aleph) -> None:
        _write_sample_page(aleph, slug="postgres")
        meta = aleph.find_page("postgres")
        assert meta is not None
        assert meta.title == "Test Concept"

    def test_find_page_not_found(self, aleph: Aleph) -> None:
        assert aleph.find_page("nonexistent") is None

    def test_find_by_title(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        meta = aleph.find_by_title_or_alias("Test Concept")
        assert meta is not None

    def test_find_by_alias(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        meta = aleph.find_by_title_or_alias("TC")
        assert meta is not None

    def test_find_case_insensitive(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        assert aleph.find_by_title_or_alias("test concept") is not None
        assert aleph.find_by_title_or_alias("tc") is not None

    def test_find_by_title_not_found(self, aleph: Aleph) -> None:
        assert aleph.find_by_title_or_alias("nonexistent") is None

    def test_read_index_empty(self, aleph: Aleph) -> None:
        entries = aleph.read_index()
        assert entries == []

    def test_read_index_with_entries(self, aleph: Aleph) -> None:
        aleph.update_index(
            [
                IndexEntry(
                    page_link="[[test-concept]]",
                    type="concept",
                    wing_sub="technology / ai",
                    summary="A test concept",
                    date="2026-05-07",
                ),
            ]
        )
        entries = aleph.read_index()
        assert len(entries) == 1
        assert entries[0].page_link == "[[test-concept]]"


# ============================================================
# Search
# ============================================================


class TestSearch:
    def test_search_by_title(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        hits = aleph.search("Test Concept")
        assert len(hits) >= 1
        assert hits[0].meta.title == "Test Concept"

    def test_search_by_alias(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        hits = aleph.search("TC")
        assert len(hits) >= 1

    def test_search_by_summary(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        hits = aleph.search("unit testing")
        assert len(hits) >= 1

    def test_search_by_body(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        hits = aleph.search("核心摘要")
        assert len(hits) >= 1

    def test_search_empty_query(self, aleph: Aleph) -> None:
        assert aleph.search("") == []

    def test_search_no_results(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        assert aleph.search("xyzzy nonexistent") == []

    def test_search_limit(self, aleph: Aleph) -> None:
        for i in range(5):
            aleph.write_page(
                slug=f"test-{i}",
                domain="technology",
                subcategory="ai",
                frontmatter={
                    "title": f"Test Item {i}",
                    "type": "concept",
                    "domain": "technology",
                    "subcategory": "ai",
                    "summary": f"test item number {i}",
                    "tags": [],
                },
                body=f"# Test Item {i}\n",
            )
        hits = aleph.search("test item", limit=3)
        assert len(hits) <= 3

    def test_search_title_ranked_higher(self, aleph: Aleph) -> None:
        """Title match should score higher than body match."""
        aleph.write_page(
            slug="postgres",
            domain="technology",
            subcategory="ai",
            frontmatter={
                "title": "Postgres",
                "type": "concept",
                "domain": "technology",
                "subcategory": "ai",
                "summary": "A relational database",
                "tags": [],
            },
            body="# Postgres\n\nA great database.\n",
        )
        aleph.write_page(
            slug="database-notes",
            domain="technology",
            subcategory="ai",
            frontmatter={
                "title": "Database Notes",
                "type": "concept",
                "domain": "technology",
                "subcategory": "ai",
                "summary": "Notes about databases",
                "tags": [],
            },
            body="# Database Notes\n\nWe considered Postgres for the project.\n",
        )
        hits = aleph.search("Postgres")
        assert len(hits) >= 2
        # Title match (postgres) should be first
        assert hits[0].meta.title == "Postgres"


# ============================================================
# CJK search — T1.13.5-class fix for wiki.py
# ============================================================


def _write_chinese_page(
    aleph: Aleph,
    slug: str,
    title: str,
    summary: str,
    body: str = "",
    *,
    aliases: list[str] | None = None,
) -> None:
    """Helper: write a page with CJK content."""
    aleph.write_page(
        slug=slug,
        domain="work",
        subcategory="people",
        frontmatter={
            "title": title,
            "type": "person",
            "domain": "work",
            "subcategory": "people",
            "summary": summary,
            "aliases": aliases or [],
            "tags": [],
        },
        body=body or f"# {title}\n\n{summary}\n",
    )


class TestCJKSearch:
    """CJK search: Chinese queries without spaces must still match pages.

    Before fix: ``"海龙负责什么"`` → ``split()`` produces 1 token →
    substring match fails → 0 hits.

    After fix: ``_search_tokens("海龙负责什么")`` → ``{"海","龙","负","责","什","么"}``
    → ``"海" in "海龙"`` matches → hit found.
    """

    def test_cjk_no_space_finds_title(self, aleph: Aleph) -> None:
        """'海龙负责什么' must find the page titled '海龙'."""
        _write_chinese_page(aleph, "hailong", "海龙", "产品负责人，负责星图项目")

        hits = aleph.search("海龙负责什么")
        assert len(hits) >= 1
        assert hits[0].meta.title == "海龙"

    def test_cjk_single_token_still_works(self, aleph: Aleph) -> None:
        """Single CJK term (short query) still matches."""
        _write_chinese_page(aleph, "hailong", "海龙", "产品负责人")

        hits = aleph.search("海龙")
        assert len(hits) >= 1
        assert hits[0].meta.title == "海龙"

    def test_cjk_space_separated_still_works(self, aleph: Aleph) -> None:
        """Space-separated CJK (existing behavior) still works."""
        _write_chinese_page(aleph, "hailong", "海龙", "产品负责人，负责星图项目")

        hits = aleph.search("海龙 负责")
        assert len(hits) >= 1
        assert hits[0].meta.title == "海龙"

    def test_cjk_summary_match(self, aleph: Aleph) -> None:
        """CJK query matches page summary, not just title."""
        _write_chinese_page(aleph, "data-gov", "数据治理", "数据中心底座建设方案")

        hits = aleph.search("底座建设")
        assert len(hits) >= 1
        assert hits[0].meta.title == "数据治理"

    def test_cjk_body_match(self, aleph: Aleph) -> None:
        """CJK query matches page body text."""
        _write_chinese_page(
            aleph,
            "starmap",
            "星图计划",
            "智能体协作平台",
            body="# 星图计划\n\n海龙负责产品设计，张扬负责后端开发。\n",
        )

        hits = aleph.search("后端开发")
        assert len(hits) >= 1

    def test_cjk_title_ranked_above_body(self, aleph: Aleph) -> None:
        """Page with CJK title match ranks above body-only match."""
        _write_chinese_page(aleph, "hailong", "海龙", "产品负责人")
        _write_chinese_page(
            aleph,
            "starmap",
            "星图计划",
            "智能体协作平台",
            body="# 星图计划\n\n海龙负责产品设计。\n",
        )

        hits = aleph.search("海龙")
        assert len(hits) >= 2
        # Title match ("海龙") should rank above body-only match
        assert hits[0].meta.title == "海龙"

    def test_mixed_cjk_latin(self, aleph: Aleph) -> None:
        """Mixed CJK + Latin query works."""
        _write_chinese_page(
            aleph,
            "postgres-decision",
            "Postgres选型决策",
            "选择Postgres作为主数据库",
        )

        hits = aleph.search("Postgres选型")
        assert len(hits) >= 1

    def test_cjk_alias_match(self, aleph: Aleph) -> None:
        """CJK query matches page aliases."""
        _write_chinese_page(
            aleph,
            "hailong",
            "海龙",
            "产品负责人",
            aliases=["产品经理海龙"],
        )

        hits = aleph.search("产品经理")
        assert len(hits) >= 1

    def test_japanese_hiragana(self, aleph: Aleph) -> None:
        """Japanese hiragana tokenized per-character."""
        _write_chinese_page(aleph, "japan-test", "テスト概念", "これはテストです")

        hits = aleph.search("テスト")
        assert len(hits) >= 1


# ============================================================
# Write operations
# ============================================================


class TestWriteOperations:
    def test_write_creates_file(self, aleph: Aleph) -> None:
        rel = _write_sample_page(aleph)
        full = aleph.root / rel
        assert full.exists()
        text = full.read_text()
        assert "title: Test Concept" in text
        assert "unit testing" in text

    def test_write_creates_subdirectory(self, aleph: Aleph) -> None:
        aleph.write_page(
            slug="new-thing",
            domain="life",
            subcategory="travel",
            frontmatter={
                "title": "New Thing",
                "type": "concept",
                "domain": "life",
                "subcategory": "travel",
                "summary": "test",
                "tags": [],
            },
            body="# New Thing\n",
        )
        assert (aleph.root / "wings" / "life" / "travel" / "new-thing.md").exists()

    def test_write_duplicate_raises(self, aleph: Aleph) -> None:
        _write_sample_page(aleph)
        with pytest.raises(FileExistsError):
            _write_sample_page(aleph)

    def test_update_frontmatter(self, aleph: Aleph) -> None:
        rel = _write_sample_page(aleph)
        aleph.update_page(
            rel,
            frontmatter_updates={
                "related": ["[[new-page]]"],
                "last_verified": "2026-05-08",
            },
        )
        meta, _ = aleph.read_page(rel)
        assert meta is not None
        assert "[[new-page]]" in meta.related
        assert meta.last_verified == "2026-05-08"

    def test_update_extends_lists(self, aleph: Aleph) -> None:
        rel = _write_sample_page(aleph)
        aleph.update_page(rel, frontmatter_updates={"sources": ["[[sources/new]]"]})
        aleph.update_page(rel, frontmatter_updates={"sources": ["[[sources/another]]"]})
        meta, _ = aleph.read_page(rel)
        assert meta is not None
        assert len(meta.sources) == 2

    def test_update_deduplicates_lists(self, aleph: Aleph) -> None:
        rel = _write_sample_page(aleph)
        aleph.update_page(rel, frontmatter_updates={"sources": ["[[sources/new]]"]})
        aleph.update_page(rel, frontmatter_updates={"sources": ["[[sources/new]]"]})
        meta, _ = aleph.read_page(rel)
        assert meta is not None
        assert len(meta.sources) == 1

    def test_update_append_history(self, aleph: Aleph) -> None:
        rel = _write_sample_page(aleph)
        aleph.update_page(rel, append_history="- 2026-05-08 updated from itsme intake")
        _, body = aleph.read_page(rel)
        assert "2026-05-08 updated from itsme intake" in body

    def test_update_nonexistent_raises(self, aleph: Aleph) -> None:
        with pytest.raises(FileNotFoundError):
            aleph.update_page("wings/nope.md", frontmatter_updates={"title": "x"})


# ============================================================
# Index and log
# ============================================================


class TestIndexAndLog:
    def test_update_index_adds_entries(self, aleph: Aleph) -> None:
        aleph.update_index(
            [
                IndexEntry("[[page-a]]", "concept", "technology / ai", "Page A", "2026-05-07"),
                IndexEntry("[[page-b]]", "person", "work / people", "Page B", "2026-05-07"),
            ]
        )
        entries = aleph.read_index()
        assert len(entries) == 2

    def test_update_index_upserts(self, aleph: Aleph) -> None:
        aleph.update_index(
            [
                IndexEntry("[[page-a]]", "concept", "technology / ai", "old summary", "2026-05-07"),
            ]
        )
        aleph.update_index(
            [
                IndexEntry("[[page-a]]", "concept", "technology / ai", "new summary", "2026-05-08"),
            ]
        )
        entries = aleph.read_index()
        assert len(entries) == 1
        assert entries[0].summary == "new summary"

    def test_append_log(self, aleph: Aleph) -> None:
        aleph.append_log(action="INGEST", source="itsme-test", summary="新增 1 页")
        text = (aleph.root / "log.md").read_text()
        assert "[INGEST]" in text
        assert "itsme-test" in text
        assert "新增 1 页" in text

    def test_append_log_multiple(self, aleph: Aleph) -> None:
        aleph.append_log(action="INGEST", source="src1", summary="first")
        aleph.append_log(action="UPDATE", source="src2", summary="second")
        text = (aleph.root / "log.md").read_text()
        assert text.count("[INGEST]") == 1
        assert text.count("[UPDATE]") == 1


# ============================================================
# Frontmatter parsing edge cases
# ============================================================


class TestFrontmatterParsing:
    def test_page_without_frontmatter_skipped(self, aleph: Aleph) -> None:
        """Pages without YAML frontmatter are ignored."""
        (aleph.root / "wings" / "technology").mkdir(parents=True, exist_ok=True)
        (aleph.root / "wings" / "technology" / "plain.md").write_text(
            "# Just a plain page\n\nNo frontmatter here.\n"
        )
        pages = aleph.list_pages()
        assert len(pages) == 0

    def test_page_with_empty_frontmatter(self, aleph: Aleph) -> None:
        (aleph.root / "wings" / "technology").mkdir(parents=True, exist_ok=True)
        (aleph.root / "wings" / "technology" / "empty-fm.md").write_text(
            "---\n---\n\n# Empty frontmatter\n"
        )
        pages = aleph.list_pages()
        # Empty frontmatter = empty dict = skipped (no required fields fail gracefully)
        assert len(pages) == 0

    def test_aliases_none_handled(self, aleph: Aleph) -> None:
        """aliases: null in YAML should become empty list."""
        (aleph.root / "wings" / "technology").mkdir(parents=True, exist_ok=True)
        (aleph.root / "wings" / "technology" / "null-alias.md").write_text(
            "---\ntitle: Null Alias Test\ntype: concept\n"
            "domain: technology\nsubcategory: ai\n"
            "summary: test\naliases:\ntags: []\n---\n\n# Test\n"
        )
        pages = aleph.list_pages()
        assert len(pages) == 1
        assert pages[0].aliases == []


# ============================================================
# Path safety
# ============================================================


class TestPathSafety:
    def test_write_path_escape_blocked(self, aleph: Aleph) -> None:
        """Slug with ../ cannot escape wiki root."""
        with pytest.raises((ValueError, FileExistsError)):
            aleph.write_page(
                slug="../../../etc/evil",
                domain="technology",
                subcategory="ai",
                frontmatter={
                    "title": "Evil",
                    "type": "concept",
                    "domain": "technology",
                    "subcategory": "ai",
                    "summary": "x",
                    "tags": [],
                },
                body="# Evil\n",
            )

    def test_read_path_escape_blocked(self, aleph: Aleph) -> None:
        with pytest.raises(ValueError, match="escapes Aleph root"):
            aleph.read_page("../../etc/passwd")

    def test_update_path_escape_blocked(self, aleph: Aleph) -> None:
        with pytest.raises(ValueError, match="escapes Aleph root"):
            aleph.update_page("../../etc/passwd", frontmatter_updates={"title": "x"})

    def test_duplicate_slug_across_wings_blocked(self, aleph: Aleph) -> None:
        """Same slug in different wings is rejected on create."""
        aleph.write_page(
            slug="shared-name",
            domain="technology",
            subcategory="ai",
            frontmatter={
                "title": "A",
                "type": "concept",
                "domain": "technology",
                "subcategory": "ai",
                "summary": "x",
                "tags": [],
            },
            body="# A\n",
        )
        with pytest.raises(FileExistsError, match="shared-name"):
            aleph.write_page(
                slug="shared-name",
                domain="work",
                subcategory="projects",
                frontmatter={
                    "title": "B",
                    "type": "project",
                    "domain": "work",
                    "subcategory": "projects",
                    "summary": "y",
                    "tags": [],
                },
                body="# B\n",
            )


# ============================================================
# Index sanitization
# ============================================================


class TestIndexSanitization:
    def test_pipe_in_summary_escaped(self, aleph: Aleph) -> None:
        """Pipe chars in summary don't break the table."""
        aleph.update_index(
            [
                IndexEntry("[[test]]", "concept", "tech / ai", "A | B summary", "2026-05-07"),
            ]
        )
        entries = aleph.read_index()
        assert len(entries) == 1
        assert "A" in entries[0].summary

    def test_newline_in_summary_collapsed(self, aleph: Aleph) -> None:
        aleph.update_index(
            [
                IndexEntry("[[test]]", "concept", "tech / ai", "line1\nline2", "2026-05-07"),
            ]
        )
        text = (aleph.root / "index.md").read_text()
        # Find the table row containing [[test]] and verify newlines were collapsed
        for line in text.splitlines():
            if "[[test]]" in line:
                assert "line1" in line
                assert "line2" in line
                # Both parts on one line = newline was collapsed
                break
        else:
            pytest.fail("Could not find [[test]] row in index.md")
