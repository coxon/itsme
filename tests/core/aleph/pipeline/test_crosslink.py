"""Tests for Aleph crosslink pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.aleph.pipeline.crosslink import (
    _build_target_map,
    _crosslink_body,
    _make_pattern,
    _shield_protected,
    _unshield,
    crosslink,
)
from itsme.core.aleph.wiki import Aleph, PageMeta

# ================================================================ fixtures


@pytest.fixture()
def wiki_dir(tmp_path: Path) -> Path:
    """Create a minimal Aleph wiki directory."""
    root = tmp_path / "aleph"
    root.mkdir()
    (root / "dna.md").write_text("# Aleph DNA\n")
    (root / "index.md").write_text("# Aleph Index\n")
    wings = root / "wings"
    wings.mkdir()
    return root


def _write_page(
    root: Path,
    *,
    slug: str,
    domain: str = "work",
    subcategory: str = "projects",
    title: str = "",
    aliases: list[str] | None = None,
    summary: str = "",
    body: str = "",
) -> Path:
    """Write a wiki page file, return path relative to root."""
    title = title or slug
    aliases = aliases or []
    alias_str = ", ".join(f'"{a}"' for a in aliases)

    page_dir = root / "wings" / domain / subcategory
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / f"{slug}.md"

    # Build content without textwrap.dedent — body may contain multiline
    # strings (dataviewjs etc.) that break common-indent detection.
    lines = [
        "---",
        f"title: {title}",
        "type: project",
        f"domain: {domain}",
        f"subcategory: {subcategory}",
        f"aliases: [{alias_str}]",
        f"summary: {summary}",
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
    content = "\n".join(lines)
    page_path.write_text(content, encoding="utf-8")
    return page_path


# ================================================================ _shield_protected


class TestShieldProtected:
    def test_fenced_code_block(self) -> None:
        body = "before\n```python\nfoo = bar\n```\nafter"
        shielded, originals = _shield_protected(body)
        assert "```" not in shielded
        assert len(originals) == 1
        assert "foo = bar" in originals[0]

    def test_inline_code(self) -> None:
        body = "use `海龙` in code"
        shielded, originals = _shield_protected(body)
        assert "`海龙`" not in shielded
        assert len(originals) == 1

    def test_existing_wikilink(self) -> None:
        body = "see [[starmap|星图计划]] for details"
        shielded, originals = _shield_protected(body)
        assert "[[" not in shielded
        assert len(originals) == 1

    def test_roundtrip(self) -> None:
        body = "a [[link]] and `code` and\n```\nblock\n```\nend"
        shielded, originals = _shield_protected(body)
        restored = _unshield(shielded, originals)
        assert restored == body

    def test_multiline_callout(self) -> None:
        """Multi-line Obsidian callouts must be fully shielded."""
        body = "before\n> [!info]\n> line one\n> line two\nafter"
        shielded, originals = _shield_protected(body)
        assert "> [!info]" not in shielded
        assert "line one" not in shielded
        assert "line two" not in shielded
        assert len(originals) == 1
        assert "> line two" in originals[0]

    def test_multiline_callout_roundtrip(self) -> None:
        body = "top\n> [!warning]\n> caution\n> really\nnext para"
        shielded, originals = _shield_protected(body)
        restored = _unshield(shielded, originals)
        assert restored == body


# ================================================================ _build_target_map


class TestBuildTargetMap:
    def test_sorted_longest_first(self) -> None:
        pages = [
            PageMeta(
                path=Path("wings/work/projects/starmap.md"),
                title="星图",
                type="project",
                domain="work",
                subcategory="projects",
                summary="",
            ),
            PageMeta(
                path=Path("wings/work/projects/starmap-plan.md"),
                title="星图计划",
                type="project",
                domain="work",
                subcategory="projects",
                summary="",
            ),
        ]
        targets = _build_target_map(pages)
        # 星图计划 (3 chars) should come before 星图 (2 chars)
        assert targets[0][0] == "星图计划"
        assert targets[1][0] == "星图"

    def test_alias_included(self) -> None:
        pages = [
            PageMeta(
                path=Path("wings/tech/ai/rag.md"),
                title="RAG",
                type="concept",
                domain="technology",
                subcategory="ai",
                summary="",
                aliases=["retrieval augmented generation"],
            ),
        ]
        targets = _build_target_map(pages)
        texts = [t[0] for t in targets]
        assert "RAG" in texts
        assert "retrieval augmented generation" in texts

    def test_dedup_by_match_text(self) -> None:
        """Two pages with the same title — only first wins."""
        pages = [
            PageMeta(
                path=Path("wings/a/b/foo.md"),
                title="Foo",
                type="concept",
                domain="a",
                subcategory="b",
                summary="",
            ),
            PageMeta(
                path=Path("wings/a/b/bar.md"),
                title="Foo",  # same title as above
                type="concept",
                domain="a",
                subcategory="b",
                summary="",
            ),
        ]
        targets = _build_target_map(pages)
        assert len(targets) == 1
        assert targets[0][1] == "foo"  # first page wins


# ================================================================ _make_pattern


class TestMakePattern:
    def test_latin_word_boundary(self) -> None:
        pat = _make_pattern("RAG")
        assert pat.search("use RAG here")
        assert not pat.search("DRAGON")  # "RAG" inside "DRAGON"

    def test_latin_case_insensitive(self) -> None:
        pat = _make_pattern("SpaceX")
        assert pat.search("invested in spacex")

    def test_cjk_no_boundary(self) -> None:
        pat = _make_pattern("海龙")
        assert pat.search("负责人海龙负责产品")

    def test_mixed(self) -> None:
        pat = _make_pattern("AI平台")
        assert pat.search("构建AI平台的方案")


# ================================================================ _crosslink_body


class TestCrosslinkBody:
    def test_basic_link_insertion(self) -> None:
        targets = [("海龙", "hai-long", "海龙")]
        body = "# Title\n\n负责人是海龙。"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="starmap")
        assert count == 1
        assert "[[hai-long|海龙]]" in new_body

    def test_no_self_link(self) -> None:
        targets = [("星图", "starmap", "星图")]
        body = "# 星图\n\n这是星图项目。"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="starmap")
        assert count == 0
        assert "[[" not in new_body

    def test_first_occurrence_only(self) -> None:
        targets = [("海龙", "hai-long", "海龙")]
        body = "海龙说了，海龙做了，海龙走了。"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        assert count == 1
        assert new_body.count("[[hai-long|海龙]]") == 1
        # Remaining occurrences stay plain
        assert new_body.count("海龙") == 3  # 1 in link + 2 plain

    def test_skip_when_already_linked(self) -> None:
        """When a [[slug...]] link already exists, don't add more."""
        targets = [("海龙", "hai-long", "海龙")]
        body = "已经有 [[hai-long|海龙]] 链接。以及海龙另外说了。"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        # Already linked → skip all plain-text occurrences
        assert count == 0
        # Both occurrences remain as-is
        assert "[[hai-long|海龙]]" in new_body
        assert new_body.count("海龙") == 2  # 1 in existing link + 1 plain

    def test_skip_code_block(self) -> None:
        targets = [("foo", "foo-page", "foo")]
        body = "说 foo 和\n```python\nfoo = 1\n```\n完了"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        assert count == 1
        assert "[[foo-page|foo]]" in new_body
        # Code block should be intact
        assert "```python\nfoo = 1\n```" in new_body

    def test_skip_inline_code(self) -> None:
        targets = [("foo", "foo-page", "foo")]
        body = "use `foo` or call foo"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        assert count == 1
        # The `foo` in inline code should stay, plain foo should be linked
        assert "`foo`" in new_body
        assert "[[foo-page|foo]]" in new_body

    def test_longest_match_first(self) -> None:
        targets = [
            ("星图计划", "starmap-plan", "星图计划"),
            ("星图", "starmap", "星图"),
        ]
        body = "参与星图计划的开发。"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        assert count == 1
        assert "[[starmap-plan|星图计划]]" in new_body
        assert "[[starmap" not in new_body or "starmap-plan" in new_body

    def test_slug_equals_display_uses_bare_link(self) -> None:
        """When slug == matched text, use [[slug]] not [[slug|slug]]."""
        targets = [("RAG", "RAG", "RAG")]
        body = "about RAG technique"
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        assert count == 1
        assert "[[RAG]]" in new_body
        assert "[[RAG|RAG]]" not in new_body

    def test_empty_body(self) -> None:
        targets = [("foo", "foo-page", "foo")]
        new_body, count = _crosslink_body("", targets=targets, self_slug="other")
        assert count == 0
        assert new_body == ""

    def test_no_targets(self) -> None:
        new_body, count = _crosslink_body("some text", targets=[], self_slug="x")
        assert count == 0
        assert new_body == "some text"

    def test_latin_word_boundary_respected(self) -> None:
        """'RAG' should not match inside 'DRAGON'."""
        targets = [("RAG", "rag", "RAG")]
        body = "DRAGON breathes fire. RAG works well."
        new_body, count = _crosslink_body(body, targets=targets, self_slug="other")
        assert count == 1
        assert "DRAGON" in new_body  # not modified
        assert "[[rag|RAG]]" in new_body


# ================================================================ crosslink (integration)


class TestCrosslinkIntegration:
    def test_crosslink_inserts_links(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="hai-long",
            domain="work",
            subcategory="people",
            title="海龙",
            summary="产品负责人",
            body="负责星图计划。",
        )
        _write_page(
            wiki_dir,
            slug="starmap",
            domain="work",
            subcategory="projects",
            title="星图计划",
            summary="前端呈现项目",
            body="负责人是海龙。需要用到 RAG 技术。",
        )
        _write_page(
            wiki_dir,
            slug="rag",
            domain="technology",
            subcategory="ai",
            title="RAG",
            summary="检索增强生成",
            body="一种 AI 技术。",
        )

        aleph = Aleph(wiki_dir)
        result = crosslink(aleph, dry_run=False)

        assert result.pages_scanned == 3
        assert result.pages_modified >= 2  # hai-long and starmap should be modified
        assert result.links_inserted >= 2

        # Verify hai-long page now links to starmap
        _, body = aleph.read_page("wings/work/people/hai-long.md")
        assert "[[starmap|星图计划]]" in body

        # Verify starmap page now links to hai-long and rag
        _, body = aleph.read_page("wings/work/projects/starmap.md")
        assert "[[hai-long|海龙]]" in body
        assert "[[rag|RAG]]" in body or "[[rag]]" in body

    def test_dry_run_no_write(self, wiki_dir: Path) -> None:
        _write_page(
            wiki_dir,
            slug="alpha",
            title="Alpha",
            body="mentions Beta here",
        )
        _write_page(
            wiki_dir,
            slug="beta",
            title="Beta",
            body="nothing to link",
        )

        aleph = Aleph(wiki_dir)
        result = crosslink(aleph, dry_run=True)

        assert result.links_inserted >= 1
        # But the file should NOT have been modified
        _, body = aleph.read_page("wings/work/projects/alpha.md")
        assert "[[beta" not in body

    def test_idempotent(self, wiki_dir: Path) -> None:
        """Running crosslink twice should not insert duplicate links."""
        _write_page(
            wiki_dir,
            slug="alice",
            domain="work",
            subcategory="people",
            title="Alice",
            body="Works with Bob.",
        )
        _write_page(
            wiki_dir,
            slug="bob",
            domain="work",
            subcategory="people",
            title="Bob",
            body="Works with Alice.",
        )

        aleph = Aleph(wiki_dir)

        r1 = crosslink(aleph, dry_run=False)
        assert r1.links_inserted >= 2

        # Second pass — existing [[links]] should be shielded
        r2 = crosslink(aleph, dry_run=False)
        assert r2.links_inserted == 0

    def test_empty_wiki(self, wiki_dir: Path) -> None:
        aleph = Aleph(wiki_dir)
        result = crosslink(aleph, dry_run=False)
        assert result.pages_scanned == 0
        assert result.links_inserted == 0

    def test_cjk_alias_linked(self, wiki_dir: Path) -> None:
        """A page's alias should also trigger crosslinks."""
        _write_page(
            wiki_dir,
            slug="rag",
            domain="technology",
            subcategory="ai",
            title="RAG",
            aliases=["检索增强生成"],
            body="一种技术。",
        )
        _write_page(
            wiki_dir,
            slug="overview",
            title="Overview",
            body="我们使用了检索增强生成来提升效果。",
        )

        aleph = Aleph(wiki_dir)
        crosslink(aleph, dry_run=False)

        _, body = aleph.read_page("wings/work/projects/overview.md")
        assert "[[rag|检索增强生成]]" in body

    def test_preserves_frontmatter(self, wiki_dir: Path) -> None:
        """Crosslink should not corrupt frontmatter."""
        _write_page(
            wiki_dir,
            slug="page-a",
            title="Page A",
            summary="summary for page A",
            body="mentions Page B here.",
        )
        _write_page(
            wiki_dir,
            slug="page-b",
            title="Page B",
            body="nothing",
        )

        aleph = Aleph(wiki_dir)
        crosslink(aleph, dry_run=False)

        meta, _ = aleph.read_page("wings/work/projects/page-a.md")
        assert meta is not None
        assert meta.title == "Page A"
        assert meta.summary == "summary for page A"

    def test_preserves_dataviewjs(self, wiki_dir: Path) -> None:
        """dataviewjs blocks inside fenced code should not be altered."""
        dvjs = '```dataviewjs\nconst p = dv.current();\ndv.paragraph("海龙");\n```'
        _write_page(
            wiki_dir,
            slug="page-dv",
            title="Page DV",
            body=f"{dvjs}\n\n海龙说了话。",
        )
        _write_page(
            wiki_dir,
            slug="hai-long",
            domain="work",
            subcategory="people",
            title="海龙",
            body="人物。",
        )

        aleph = Aleph(wiki_dir)
        crosslink(aleph, dry_run=False)

        _, body = aleph.read_page("wings/work/projects/page-dv.md")
        # dataviewjs block should be intact
        assert 'dv.paragraph("海龙")' in body
        # The plain text mention should be linked
        assert "[[hai-long|海龙]]说了话" in body
