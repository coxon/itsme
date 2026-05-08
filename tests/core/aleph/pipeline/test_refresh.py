"""Tests for Aleph refresh pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.aleph.pipeline.refresh import (
    _collapse_blanks,
    _dedup_history,
    _dedup_paragraphs,
    _normalize,
    refresh,
)
from itsme.core.aleph.wiki import Aleph

# ================================================================ fixtures


@pytest.fixture()
def wiki_dir(tmp_path: Path) -> Path:
    """Create a minimal Aleph wiki directory."""
    root = tmp_path / "aleph"
    root.mkdir()
    (root / "dna.md").write_text("# Aleph DNA\n")
    (root / "index.md").write_text("# Aleph Index\n")
    (root / "wings").mkdir()
    return root


def _write_page(
    root: Path,
    *,
    slug: str,
    domain: str = "work",
    subcategory: str = "projects",
    title: str = "",
    body: str = "",
) -> Path:
    title = title or slug
    page_dir = root / "wings" / domain / subcategory
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / f"{slug}.md"
    lines = [
        "---",
        f"title: {title}",
        "type: project",
        f"domain: {domain}",
        f"subcategory: {subcategory}",
        "aliases: []",
        "summary: test",
        "sources: []",
        "links: []",
        "related: []",
        "tags: []",
        'last_verified: "2026-05-08"',
        "---",
        "",
        f"# {title}",
        "",
        body,
        "",
    ]
    page_path.write_text("\n".join(lines), encoding="utf-8")
    return page_path


# ================================================================ _normalize


class TestNormalize:
    def test_collapse_whitespace(self) -> None:
        assert _normalize("  a  b   c  ") == "a b c"

    def test_newlines(self) -> None:
        assert _normalize("a\n\nb\nc") == "a b c"


# ================================================================ _dedup_paragraphs


class TestDedupParagraphs:
    def test_removes_exact_duplicate(self) -> None:
        body = "First paragraph.\n\nSecond paragraph.\n\nFirst paragraph."
        new_body, count = _dedup_paragraphs(body)
        assert count == 1
        assert new_body.count("First paragraph.") == 1
        assert "Second paragraph." in new_body

    def test_whitespace_normalized(self) -> None:
        body = "Same  thing.\n\nSame thing."
        new_body, count = _dedup_paragraphs(body)
        assert count == 1

    def test_preserves_code_blocks(self) -> None:
        body = "```python\nfoo = 1\n```\n\n```python\nfoo = 1\n```"
        new_body, count = _dedup_paragraphs(body)
        assert count == 0  # code blocks are never deduped

    def test_preserves_callouts(self) -> None:
        body = "> [!info] same\n> content\n\n> [!info] same\n> content"
        new_body, count = _dedup_paragraphs(body)
        assert count == 0  # callouts are never deduped

    def test_no_duplicates(self) -> None:
        body = "Alpha.\n\nBeta.\n\nGamma."
        new_body, count = _dedup_paragraphs(body)
        assert count == 0
        assert new_body == body

    def test_multiple_duplicates(self) -> None:
        body = "A.\n\nB.\n\nA.\n\nC.\n\nB."
        new_body, count = _dedup_paragraphs(body)
        assert count == 2
        # Keep first occurrence of each
        parts = [p.strip() for p in new_body.split("\n\n") if p.strip()]
        assert parts == ["A.", "B.", "C."]

    def test_empty_body(self) -> None:
        new_body, count = _dedup_paragraphs("")
        assert count == 0

    def test_cjk_paragraphs(self) -> None:
        body = "海龙负责产品。\n\n星图项目进展。\n\n海龙负责产品。"
        new_body, count = _dedup_paragraphs(body)
        assert count == 1
        assert new_body.count("海龙负责产品。") == 1


# ================================================================ _dedup_history


class TestDedupHistory:
    def test_removes_duplicate_entries(self) -> None:
        body = (
            "# Title\n\nContent.\n\n"
            "## History\n"
            "- 2026-05-01 创建\n"
            "- 2026-05-02 更新\n"
            "- 2026-05-01 创建\n"
        )
        new_body, count = _dedup_history(body)
        assert count == 1
        assert new_body.count("2026-05-01 创建") == 1
        assert "2026-05-02 更新" in new_body

    def test_no_history_section(self) -> None:
        body = "# Title\n\nContent only."
        new_body, count = _dedup_history(body)
        assert count == 0
        assert new_body == body

    def test_no_duplicates(self) -> None:
        body = "## History\n- 2026-05-01 创建\n- 2026-05-02 更新\n"
        new_body, count = _dedup_history(body)
        assert count == 0

    def test_preserves_non_list_lines(self) -> None:
        body = "## History\n" "Some intro.\n" "- 2026-05-01 创建\n" "- 2026-05-01 创建\n"
        new_body, count = _dedup_history(body)
        assert count == 1
        assert "Some intro." in new_body

    def test_whitespace_normalized(self) -> None:
        body = (
            "## History\n" "- 2026-05-01  创建，来源:  itsme\n" "- 2026-05-01 创建，来源: itsme\n"
        )
        new_body, count = _dedup_history(body)
        assert count == 1


# ================================================================ _collapse_blanks


class TestCollapseBlanks:
    def test_collapse_many_blank_lines(self) -> None:
        text = "a\n\n\n\n\nb"
        assert _collapse_blanks(text) == "a\n\n\nb"

    def test_no_collapse_needed(self) -> None:
        text = "a\n\nb"
        assert _collapse_blanks(text) == "a\n\nb"


# ================================================================ refresh (integration)


class TestRefreshIntegration:
    def test_refresh_removes_paragraph_dupes(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="test-page",
            title="Test",
            body="First fact.\n\nSecond fact.\n\nFirst fact.",
        )

        aleph = Aleph(wiki_dir)
        result = refresh(aleph, dry_run=False)

        assert result.pages_scanned == 1
        assert result.pages_modified == 1
        assert result.paragraphs_removed == 1

        _, body = aleph.read_page("wings/work/projects/test-page.md")
        assert body.count("First fact.") == 1

    def test_refresh_removes_history_dupes(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="test-hist",
            title="Test",
            body=(
                "Content.\n\n"
                "## History\n"
                "- 2026-05-01 创建\n"
                "- 2026-05-02 更新\n"
                "- 2026-05-01 创建\n"
            ),
        )

        aleph = Aleph(wiki_dir)
        result = refresh(aleph, dry_run=False)

        assert result.history_dupes_removed == 1
        _, body = aleph.read_page("wings/work/projects/test-hist.md")
        assert body.count("2026-05-01 创建") == 1

    def test_dry_run_no_write(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="dry",
            title="Dry",
            body="Dup.\n\nDup.",
        )

        aleph = Aleph(wiki_dir)
        result = refresh(aleph, dry_run=True)

        assert result.paragraphs_removed == 1
        # File should NOT have been modified
        _, body = aleph.read_page("wings/work/projects/dry.md")
        assert body.count("Dup.") == 2

    def test_no_changes_needed(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="clean",
            title="Clean",
            body="Unique paragraph.\n\n## History\n- 2026-05-01 创建\n",
        )

        aleph = Aleph(wiki_dir)
        result = refresh(aleph, dry_run=False)

        assert result.pages_modified == 0

    def test_preserves_frontmatter(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="fm-test",
            title="FM Test",
            body="Dup.\n\nDup.",
        )

        aleph = Aleph(wiki_dir)
        refresh(aleph, dry_run=False)

        meta, _ = aleph.read_page("wings/work/projects/fm-test.md")
        assert meta is not None
        assert meta.title == "FM Test"

    def test_idempotent(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="idem",
            title="Idem",
            body="A.\n\nB.\n\nA.",
        )

        aleph = Aleph(wiki_dir)
        r1 = refresh(aleph, dry_run=False)
        assert r1.paragraphs_removed == 1

        r2 = refresh(aleph, dry_run=False)
        assert r2.paragraphs_removed == 0
        assert r2.pages_modified == 0
