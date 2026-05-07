"""Tests for dual-engine search — wiki + MemPalace.

Verifies:
- MemPalace-only hits (no Aleph)
- Wiki + MemPalace merged hits
- Wiki hits ranked before MemPalace
- Empty queries → empty results
- Limit enforcement
"""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.wiki import Aleph
from itsme.core.search import dual_search


@pytest.fixture
def adapter() -> InMemoryMemPalaceAdapter:
    return InMemoryMemPalaceAdapter()


@pytest.fixture
def aleph(tmp_path: Path) -> Aleph:
    """Create a minimal test wiki."""
    aleph_root = tmp_path / "aleph"
    aleph_root.mkdir()
    (aleph_root / "dna.md").write_text("# DNA\n")
    (aleph_root / "wings").mkdir()
    (aleph_root / "sources").mkdir()
    return Aleph(aleph_root)


def _write_mp(adapter: InMemoryMemPalaceAdapter, content: str) -> str:
    """Write to MemPalace and return drawer_id."""
    res = adapter.write(content=content, wing="wing_test", room="room_general")
    return res.drawer_id


def _write_wiki_page(aleph: Aleph, slug: str, title: str, summary: str) -> None:
    """Write a simple wiki page."""
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
        body=f"# {title}\n\n{summary}\n",
    )


# ============================================================
# Basic dual-engine scenarios
# ============================================================


class TestDualSearch:
    def test_mempalace_only_hit(self, adapter: InMemoryMemPalaceAdapter) -> None:
        """MemPalace has the answer, no Aleph."""
        _write_mp(adapter, "We decided to deploy on Monday morning")

        hits = dual_search("deploy Monday", adapter=adapter, wing="wing_test", limit=5)

        mp_hits = [h for h in hits if h.kind == "verbatim"]
        assert len(mp_hits) >= 1
        assert "deploy" in mp_hits[0].content

    def test_wiki_and_mempalace_hit(
        self, adapter: InMemoryMemPalaceAdapter, aleph: Aleph
    ) -> None:
        """Both engines return results — merged correctly."""
        _write_mp(adapter, "We discussed database options last week")
        _write_wiki_page(
            aleph, "postgres", "Postgres", "Relational database for concurrent writes"
        )

        hits = dual_search("database", adapter=adapter, aleph=aleph, wing="wing_test", limit=5)

        kinds = {h.kind for h in hits}
        assert "wiki" in kinds
        assert "verbatim" in kinds

    def test_wiki_ranked_before_mempalace(
        self, adapter: InMemoryMemPalaceAdapter, aleph: Aleph
    ) -> None:
        """Vault hits appear before MemPalace gap-fills."""
        _write_mp(adapter, "Redis is used for caching in production")
        _write_wiki_page(aleph, "redis", "Redis", "In-memory cache layer for production")

        hits = dual_search("Redis caching", adapter=adapter, aleph=aleph, wing="wing_test", limit=5)

        assert len(hits) >= 2
        # First hit should be wiki
        assert hits[0].kind == "wiki"

    def test_no_aleph_degrades_to_mempalace_only(self, adapter: InMemoryMemPalaceAdapter) -> None:
        """When aleph=None, behaves like verbatim search."""
        _write_mp(adapter, "Important decision about deployment")

        hits = dual_search("deployment", adapter=adapter, aleph=None, wing="wing_test", limit=5)

        assert len(hits) >= 1
        assert all(h.kind == "verbatim" for h in hits)


# ============================================================
# Edge cases
# ============================================================


class TestEdgeCases:
    def test_empty_query(self, adapter: InMemoryMemPalaceAdapter) -> None:
        assert dual_search("", adapter=adapter, limit=5) == []

    def test_whitespace_query(self, adapter: InMemoryMemPalaceAdapter) -> None:
        assert dual_search("   ", adapter=adapter, limit=5) == []

    def test_limit_respected(self, adapter: InMemoryMemPalaceAdapter) -> None:
        """Results never exceed limit."""
        for i in range(10):
            _write_mp(adapter, f"item {i} about testing")

        hits = dual_search("test", adapter=adapter, wing="wing_test", limit=3)
        assert len(hits) <= 3

    def test_no_results(self, adapter: InMemoryMemPalaceAdapter) -> None:
        """Query that matches nothing."""
        hits = dual_search("xyzzy nonexistent term", adapter=adapter, wing="wing_test", limit=5)
        assert hits == []


# ============================================================
# SearchHit data integrity
# ============================================================


class TestSearchHitStructure:
    def test_wiki_hit_has_metadata(
        self, adapter: InMemoryMemPalaceAdapter, aleph: Aleph
    ) -> None:
        """Wiki hits carry structured metadata."""
        _write_wiki_page(aleph, "postgres", "Postgres", "Relational database")

        hits = dual_search("Postgres", adapter=adapter, aleph=aleph, limit=5)
        wiki_hits = [h for h in hits if h.kind == "wiki"]
        assert len(wiki_hits) == 1
        assert wiki_hits[0].metadata is not None
        assert "title" in wiki_hits[0].metadata
        assert wiki_hits[0].ref.startswith("wiki:")

    def test_mp_hit_has_no_metadata(self, adapter: InMemoryMemPalaceAdapter) -> None:
        """MemPalace hits don't carry structured metadata."""
        _write_mp(adapter, "Some raw content about testing")

        hits = dual_search("testing", adapter=adapter, limit=5)
        mp_hits = [h for h in hits if h.kind == "verbatim"]
        assert len(mp_hits) >= 1
        assert mp_hits[0].metadata is None

    def test_ref_format(self, adapter: InMemoryMemPalaceAdapter, aleph: Aleph) -> None:
        """Refs follow the expected format."""
        _write_mp(adapter, "Ref format test content")
        _write_wiki_page(aleph, "ref-test", "Ref Test", "Testing ref format")

        hits = dual_search(
            "ref format test", adapter=adapter, aleph=aleph, wing="wing_test", limit=5
        )

        for h in hits:
            if h.kind == "wiki":
                assert h.ref.startswith("wiki:")
            elif h.kind == "verbatim":
                assert h.ref.startswith("mempalace:")
