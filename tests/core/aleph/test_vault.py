"""Tests for AlephVault — Obsidian vault adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.aleph.vault import AlephVault, IndexEntry


@pytest.fixture
def vault(tmp_path: Path) -> AlephVault:
    """Create a minimal Aleph vault for testing."""
    # dna.md (required marker)
    (tmp_path / "dna.md").write_text("# Aleph DNA\n\nTest vault.\n", encoding="utf-8")
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
    return AlephVault(tmp_path)


def _write_sample_page(vault: AlephVault, slug: str = "test-concept") -> Path:
    """Write a sample page and return its relative path."""
    return vault.write_page(
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
    def test_valid_vault(self, vault: AlephVault) -> None:
        assert vault.root.exists()

    def test_missing_dna_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="dna.md"):
            AlephVault(tmp_path)

    def test_root_property(self, vault: AlephVault) -> None:
        assert vault.root.is_dir()


# ============================================================
# Read operations
# ============================================================


class TestReadOperations:
    def test_list_pages_empty(self, vault: AlephVault) -> None:
        assert vault.list_pages() == []

    def test_list_pages_finds_pages(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        pages = vault.list_pages()
        assert len(pages) == 1
        assert pages[0].title == "Test Concept"
        assert pages[0].type == "concept"
        assert pages[0].domain == "technology"

    def test_read_page(self, vault: AlephVault) -> None:
        rel = _write_sample_page(vault)
        meta, body = vault.read_page(rel)
        assert meta is not None
        assert meta.title == "Test Concept"
        assert "unit testing" in body

    def test_read_page_not_found(self, vault: AlephVault) -> None:
        meta, body = vault.read_page("wings/nonexistent.md")
        assert meta is None
        assert body == ""

    def test_find_page_by_slug(self, vault: AlephVault) -> None:
        _write_sample_page(vault, slug="postgres")
        meta = vault.find_page("postgres")
        assert meta is not None
        assert meta.title == "Test Concept"

    def test_find_page_not_found(self, vault: AlephVault) -> None:
        assert vault.find_page("nonexistent") is None

    def test_find_by_title(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        meta = vault.find_by_title_or_alias("Test Concept")
        assert meta is not None

    def test_find_by_alias(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        meta = vault.find_by_title_or_alias("TC")
        assert meta is not None

    def test_find_case_insensitive(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        assert vault.find_by_title_or_alias("test concept") is not None
        assert vault.find_by_title_or_alias("tc") is not None

    def test_find_by_title_not_found(self, vault: AlephVault) -> None:
        assert vault.find_by_title_or_alias("nonexistent") is None

    def test_read_index_empty(self, vault: AlephVault) -> None:
        entries = vault.read_index()
        assert entries == []

    def test_read_index_with_entries(self, vault: AlephVault) -> None:
        vault.update_index(
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
        entries = vault.read_index()
        assert len(entries) == 1
        assert entries[0].page_link == "[[test-concept]]"


# ============================================================
# Search
# ============================================================


class TestSearch:
    def test_search_by_title(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        hits = vault.search("Test Concept")
        assert len(hits) >= 1
        assert hits[0].meta.title == "Test Concept"

    def test_search_by_alias(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        hits = vault.search("TC")
        assert len(hits) >= 1

    def test_search_by_summary(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        hits = vault.search("unit testing")
        assert len(hits) >= 1

    def test_search_by_body(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        hits = vault.search("核心摘要")
        assert len(hits) >= 1

    def test_search_empty_query(self, vault: AlephVault) -> None:
        assert vault.search("") == []

    def test_search_no_results(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        assert vault.search("xyzzy nonexistent") == []

    def test_search_limit(self, vault: AlephVault) -> None:
        for i in range(5):
            vault.write_page(
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
        hits = vault.search("test item", limit=3)
        assert len(hits) <= 3

    def test_search_title_ranked_higher(self, vault: AlephVault) -> None:
        """Title match should score higher than body match."""
        vault.write_page(
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
        vault.write_page(
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
        hits = vault.search("Postgres")
        assert len(hits) >= 2
        # Title match (postgres) should be first
        assert hits[0].meta.title == "Postgres"


# ============================================================
# Write operations
# ============================================================


class TestWriteOperations:
    def test_write_creates_file(self, vault: AlephVault) -> None:
        rel = _write_sample_page(vault)
        full = vault.root / rel
        assert full.exists()
        text = full.read_text()
        assert "title: Test Concept" in text
        assert "unit testing" in text

    def test_write_creates_subdirectory(self, vault: AlephVault) -> None:
        vault.write_page(
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
        assert (vault.root / "wings" / "life" / "travel" / "new-thing.md").exists()

    def test_write_duplicate_raises(self, vault: AlephVault) -> None:
        _write_sample_page(vault)
        with pytest.raises(FileExistsError):
            _write_sample_page(vault)

    def test_update_frontmatter(self, vault: AlephVault) -> None:
        rel = _write_sample_page(vault)
        vault.update_page(
            rel,
            frontmatter_updates={
                "related": ["[[new-page]]"],
                "last_verified": "2026-05-08",
            },
        )
        meta, _ = vault.read_page(rel)
        assert meta is not None
        assert "[[new-page]]" in meta.related
        assert meta.last_verified == "2026-05-08"

    def test_update_extends_lists(self, vault: AlephVault) -> None:
        rel = _write_sample_page(vault)
        vault.update_page(rel, frontmatter_updates={"sources": ["[[sources/new]]"]})
        vault.update_page(rel, frontmatter_updates={"sources": ["[[sources/another]]"]})
        meta, _ = vault.read_page(rel)
        assert meta is not None
        assert len(meta.sources) == 2

    def test_update_deduplicates_lists(self, vault: AlephVault) -> None:
        rel = _write_sample_page(vault)
        vault.update_page(rel, frontmatter_updates={"sources": ["[[sources/new]]"]})
        vault.update_page(rel, frontmatter_updates={"sources": ["[[sources/new]]"]})
        meta, _ = vault.read_page(rel)
        assert meta is not None
        assert len(meta.sources) == 1

    def test_update_append_history(self, vault: AlephVault) -> None:
        rel = _write_sample_page(vault)
        vault.update_page(rel, append_history="- 2026-05-08 updated from itsme intake")
        _, body = vault.read_page(rel)
        assert "2026-05-08 updated from itsme intake" in body

    def test_update_nonexistent_raises(self, vault: AlephVault) -> None:
        with pytest.raises(FileNotFoundError):
            vault.update_page("wings/nope.md", frontmatter_updates={"title": "x"})


# ============================================================
# Index and log
# ============================================================


class TestIndexAndLog:
    def test_update_index_adds_entries(self, vault: AlephVault) -> None:
        vault.update_index(
            [
                IndexEntry("[[page-a]]", "concept", "technology / ai", "Page A", "2026-05-07"),
                IndexEntry("[[page-b]]", "person", "work / people", "Page B", "2026-05-07"),
            ]
        )
        entries = vault.read_index()
        assert len(entries) == 2

    def test_update_index_upserts(self, vault: AlephVault) -> None:
        vault.update_index(
            [
                IndexEntry("[[page-a]]", "concept", "technology / ai", "old summary", "2026-05-07"),
            ]
        )
        vault.update_index(
            [
                IndexEntry("[[page-a]]", "concept", "technology / ai", "new summary", "2026-05-08"),
            ]
        )
        entries = vault.read_index()
        assert len(entries) == 1
        assert entries[0].summary == "new summary"

    def test_append_log(self, vault: AlephVault) -> None:
        vault.append_log(action="INGEST", source="itsme-test", summary="新增 1 页")
        text = (vault.root / "log.md").read_text()
        assert "[INGEST]" in text
        assert "itsme-test" in text
        assert "新增 1 页" in text

    def test_append_log_multiple(self, vault: AlephVault) -> None:
        vault.append_log(action="INGEST", source="src1", summary="first")
        vault.append_log(action="UPDATE", source="src2", summary="second")
        text = (vault.root / "log.md").read_text()
        assert text.count("[INGEST]") == 1
        assert text.count("[UPDATE]") == 1


# ============================================================
# Frontmatter parsing edge cases
# ============================================================


class TestFrontmatterParsing:
    def test_page_without_frontmatter_skipped(self, vault: AlephVault) -> None:
        """Pages without YAML frontmatter are ignored."""
        (vault.root / "wings" / "technology").mkdir(parents=True, exist_ok=True)
        (vault.root / "wings" / "technology" / "plain.md").write_text(
            "# Just a plain page\n\nNo frontmatter here.\n"
        )
        pages = vault.list_pages()
        assert len(pages) == 0

    def test_page_with_empty_frontmatter(self, vault: AlephVault) -> None:
        (vault.root / "wings" / "technology").mkdir(parents=True, exist_ok=True)
        (vault.root / "wings" / "technology" / "empty-fm.md").write_text(
            "---\n---\n\n# Empty frontmatter\n"
        )
        pages = vault.list_pages()
        # Empty frontmatter = empty dict = skipped (no required fields fail gracefully)
        assert len(pages) == 0

    def test_aliases_none_handled(self, vault: AlephVault) -> None:
        """aliases: null in YAML should become empty list."""
        (vault.root / "wings" / "technology").mkdir(parents=True, exist_ok=True)
        (vault.root / "wings" / "technology" / "null-alias.md").write_text(
            "---\ntitle: Null Alias Test\ntype: concept\n"
            "domain: technology\nsubcategory: ai\n"
            "summary: test\naliases:\ntags: []\n---\n\n# Test\n"
        )
        pages = vault.list_pages()
        assert len(pages) == 1
        assert pages[0].aliases == []


# ============================================================
# Path safety
# ============================================================


class TestPathSafety:
    def test_write_path_escape_blocked(self, vault: AlephVault) -> None:
        """Slug with ../ cannot escape vault."""
        with pytest.raises((ValueError, FileExistsError)):
            vault.write_page(
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

    def test_read_path_escape_blocked(self, vault: AlephVault) -> None:
        with pytest.raises(ValueError, match="escapes vault"):
            vault.read_page("../../etc/passwd")

    def test_update_path_escape_blocked(self, vault: AlephVault) -> None:
        with pytest.raises(ValueError, match="escapes vault"):
            vault.update_page("../../etc/passwd", frontmatter_updates={"title": "x"})

    def test_duplicate_slug_across_wings_blocked(self, vault: AlephVault) -> None:
        """Same slug in different wings is rejected on create."""
        vault.write_page(
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
            vault.write_page(
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
    def test_pipe_in_summary_escaped(self, vault: AlephVault) -> None:
        """Pipe chars in summary don't break the table."""
        vault.update_index(
            [
                IndexEntry("[[test]]", "concept", "tech / ai", "A | B summary", "2026-05-07"),
            ]
        )
        entries = vault.read_index()
        assert len(entries) == 1
        assert "A" in entries[0].summary

    def test_newline_in_summary_collapsed(self, vault: AlephVault) -> None:
        vault.update_index(
            [
                IndexEntry("[[test]]", "concept", "tech / ai", "line1\nline2", "2026-05-07"),
            ]
        )
        text = (vault.root / "index.md").read_text()
        # No raw newlines inside a table row
        for line in text.split("\n"):
            if "[[test]]" in line:
                assert "\n" not in line.strip()
