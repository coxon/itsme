"""Tests for wiki page semantic dedup pipeline (T4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.pipeline.dedup_pages import (
    _identify_page,
    _page_content,
    dedup_pages,
)
from itsme.core.aleph.wiki import Aleph, PageMeta

# ================================================================ fixtures


@pytest.fixture()
def wiki_dir(tmp_path: Path) -> Path:
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
    summary: str = "",
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
    page_path.write_text("\n".join(lines), encoding="utf-8")
    return page_path


# ================================================================ _page_content


class TestPageContent:
    def test_includes_title_summary_body(self) -> None:
        meta = PageMeta(
            path=Path("wings/work/projects/foo.md"),
            title="Foo",
            type="project",
            domain="work",
            subcategory="projects",
            summary="A foo thing",
        )
        content = _page_content(meta, "This is the body text.")
        assert "Foo" in content
        assert "A foo thing" in content
        assert "body text" in content

    def test_truncates_long_body(self) -> None:
        meta = PageMeta(
            path=Path("wings/work/projects/foo.md"),
            title="Foo",
            type="project",
            domain="work",
            subcategory="projects",
            summary="summary",
        )
        long_body = "x" * 1000
        content = _page_content(meta, long_body)
        # Body should be truncated, total content much less than 1000
        assert len(content) < 500


# ================================================================ _identify_page


class TestIdentifyPage:
    def test_finds_page_by_first_line(self) -> None:
        slug_map = {"Alpha": "alpha", "Beta": "beta"}
        assert _identify_page("Alpha\nsome summary\nbody", slug_map) == "alpha"

    def test_returns_none_for_unknown(self) -> None:
        assert _identify_page("Unknown\nbody", {"Alpha": "alpha"}) is None

    def test_handles_empty_content(self) -> None:
        assert _identify_page("", {"A": "a"}) is None


# ================================================================ dedup_pages


class TestDedupPages:
    def test_no_duplicates(self, wiki_dir: Path) -> None:
        _write_page(wiki_dir, slug="alpha", title="Alpha", summary="Unique A", body="Content A.")
        _write_page(wiki_dir, slug="beta", title="Beta", summary="Unique B", body="Content B.")

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        # Seed MemPalace with page content
        adapter.write(content="Alpha\nUnique A\nContent A.", wing="aleph", room="room_wiki")
        adapter.write(content="Beta\nUnique B\nContent B.", wing="aleph", room="room_wiki")

        result = dedup_pages(aleph, adapter, threshold=0.85)
        assert result.pages_scanned == 2
        assert result.count == 0

    def test_detects_duplicates(self, wiki_dir: Path) -> None:
        # Two pages with very similar content
        _write_page(
            wiki_dir,
            slug="ontology-a",
            title="企业本体",
            summary="企业级统一本体，承上启下支撑流程",
            body="构建企业统一本体方案。",
        )
        _write_page(
            wiki_dir,
            slug="ontology-b",
            title="企业本体项目",
            summary="构建企业统一本体，支撑流程智能体",
            body="企业本体建设方案。",
        )

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        # Seed with similar content
        adapter.write(
            content="企业本体\n企业级统一本体，承上启下支撑流程\n构建企业统一本体方案。",
            wing="aleph",
            room="room_wiki",
        )
        adapter.write(
            content="企业本体项目\n构建企业统一本体，支撑流程智能体\n企业本体建设方案。",
            wing="aleph",
            room="room_wiki",
        )

        result = dedup_pages(aleph, adapter, threshold=0.5)  # low threshold for InMemory Jaccard
        assert result.pages_scanned == 2
        assert result.count >= 1
        assert result.candidates[0].slug_a != result.candidates[0].slug_b

    def test_symmetric_dedup(self, wiki_dir: Path) -> None:
        """A↔B and B↔A should only appear once."""
        _write_page(wiki_dir, slug="a", title="Topic A", summary="same topic", body="shared body")
        _write_page(wiki_dir, slug="b", title="Topic B", summary="same topic", body="shared body")

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        adapter.write(content="Topic A\nsame topic\nshared body", wing="aleph", room="room_wiki")
        adapter.write(content="Topic B\nsame topic\nshared body", wing="aleph", room="room_wiki")

        result = dedup_pages(aleph, adapter, threshold=0.5)
        # Should deduplicate symmetric pairs
        slugs_in_candidates = set()
        for c in result.candidates:
            pair = tuple(sorted([c.slug_a, c.slug_b]))
            assert pair not in slugs_in_candidates, f"Duplicate pair: {pair}"
            slugs_in_candidates.add(pair)

    def test_skips_self_match(self, wiki_dir: Path) -> None:
        """A page matching itself in MemPalace should not be reported."""
        _write_page(wiki_dir, slug="solo", title="Solo Page", summary="unique", body="only one")

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        adapter.write(content="Solo Page\nunique\nonly one", wing="aleph", room="room_wiki")

        result = dedup_pages(aleph, adapter, threshold=0.5)
        assert result.count == 0

    def test_empty_wiki(self, wiki_dir: Path) -> None:
        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        result = dedup_pages(aleph, adapter)
        assert result.pages_scanned == 0
        assert result.count == 0

    def test_single_page(self, wiki_dir: Path) -> None:
        _write_page(wiki_dir, slug="only", title="Only", summary="s", body="b")
        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        result = dedup_pages(aleph, adapter)
        assert result.pages_scanned == 1
        assert result.count == 0

    def test_merge_candidate_fields(self, wiki_dir: Path) -> None:
        """MergeCandidate must carry both slugs and titles."""
        _write_page(wiki_dir, slug="x", title="Title X", summary="same stuff", body="same body")
        _write_page(wiki_dir, slug="y", title="Title Y", summary="same stuff", body="same body")

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        adapter.write(content="Title X\nsame stuff\nsame body", wing="aleph", room="room_wiki")
        adapter.write(content="Title Y\nsame stuff\nsame body", wing="aleph", room="room_wiki")

        result = dedup_pages(aleph, adapter, threshold=0.5)
        if result.count > 0:
            c = result.candidates[0]
            assert c.slug_a and c.slug_b
            assert c.title_a and c.title_b
            assert 0 < c.similarity <= 1.0
            assert c.slug_a != c.slug_b
