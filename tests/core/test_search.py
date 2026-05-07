"""Tests for dual-engine search — T2.19.

Verifies:
- Aleph-only hits (no MemPalace matches)
- MemPalace-only hits (no Aleph / Aleph degraded)
- Merged hits with dedup by drawer_id
- Aleph hits ranked before MemPalace gap-fills
- Empty queries → empty results
- Limit enforcement
- Score normalization
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.api import Aleph
from itsme.core.search import _normalize_fts5_rank, dual_search


@pytest.fixture
def adapter() -> InMemoryMemPalaceAdapter:
    return InMemoryMemPalaceAdapter()


@pytest.fixture
def aleph() -> Iterator[Aleph]:
    a = Aleph(":memory:")
    yield a
    a.close()


def _write_mp(adapter: InMemoryMemPalaceAdapter, content: str) -> str:
    """Write to MemPalace and return drawer_id."""
    res = adapter.write(content=content, wing="wing_test", room="room_general")
    return res.drawer_id


def _write_aleph(
    aleph: Aleph,
    summary: str,
    *,
    turn_id: str = "",
    entities: list | None = None,
    claims: list | None = None,
) -> str:
    """Write to Aleph and return extraction_id."""
    ext = aleph.write_extraction(
        turn_id=turn_id,
        raw_event_id=f"evt-{turn_id}",
        summary=summary,
        entities=entities or [],
        claims=claims or [],
        source="test",
    )
    return ext.id


# ============================================================
# Basic dual-engine scenarios
# ============================================================


class TestDualSearch:
    def test_aleph_only_hit(self, adapter, aleph) -> None:
        """Aleph has the answer but MemPalace doesn't match."""
        _write_aleph(aleph, "User chose Postgres for concurrent writes",
                     turn_id="d1",
                     entities=[{"name": "Postgres", "type": "database"}],
                     claims=["Postgres chosen for concurrent writes"])
        _write_mp(adapter, "some unrelated content about cooking")

        hits = dual_search("Postgres", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)

        assert len(hits) >= 1
        aleph_hits = [h for h in hits if h.kind == "extraction"]
        assert len(aleph_hits) == 1
        assert "Postgres" in aleph_hits[0].content

    def test_mempalace_only_hit(self, adapter, aleph) -> None:
        """MemPalace has the answer but Aleph doesn't match."""
        _write_mp(adapter, "We decided to deploy on Monday morning")
        _write_aleph(aleph, "User likes Python",
                     turn_id="d1",
                     entities=[{"name": "Python", "type": "language"}])

        hits = dual_search("deploy Monday", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)

        mp_hits = [h for h in hits if h.kind == "verbatim"]
        assert len(mp_hits) >= 1
        assert "deploy" in mp_hits[0].content

    def test_both_engines_hit_different_turns(self, adapter, aleph) -> None:
        """Both engines return different turns — no dedup needed."""
        _write_mp(adapter, "We discussed database options last week")
        _write_aleph(aleph, "Postgres selected for main DB",
                     turn_id="d2",
                     entities=[{"name": "Postgres", "type": "database"}],
                     claims=["Postgres selected"])

        hits = dual_search("database", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)

        kinds = {h.kind for h in hits}
        assert "extraction" in kinds or "verbatim" in kinds
        assert len(hits) >= 1

    def test_dedup_same_drawer_id(self, adapter, aleph) -> None:
        """Same turn matched by both engines — only one hit per drawer_id."""
        drawer_id = _write_mp(adapter, "Use Postgres for the user service")
        _write_aleph(aleph, "Postgres for user service",
                     turn_id=drawer_id,
                     entities=[{"name": "Postgres", "type": "database"}],
                     claims=["Postgres for user service"])

        hits = dual_search("Postgres", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)

        # Same drawer_id should NOT appear twice
        drawer_ids = [h.drawer_id for h in hits if h.drawer_id]
        assert len(set(drawer_ids)) == len(drawer_ids)
        # Aleph hit takes priority, MemPalace skipped for this drawer
        aleph_hits = [h for h in hits if h.kind == "extraction"]
        assert len(aleph_hits) >= 1

    def test_aleph_ranked_before_mempalace(self, adapter, aleph) -> None:
        """Aleph hits appear before MemPalace gap-fills."""
        _write_mp(adapter, "Redis is used for caching in production")
        _write_aleph(aleph, "Redis deployed as cache layer",
                     turn_id="d-aleph",
                     entities=[{"name": "Redis", "type": "database"}],
                     claims=["Redis used for caching"])

        hits = dual_search("Redis caching", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)

        if len(hits) >= 2:
            # First hit should be extraction (Aleph)
            assert hits[0].kind == "extraction"

    def test_no_aleph_degrades_to_mempalace_only(self, adapter) -> None:
        """When aleph=None, behaves like verbatim search."""
        _write_mp(adapter, "Important decision about deployment")

        hits = dual_search("deployment", adapter=adapter, aleph=None,
                           wing="wing_test", limit=5)

        assert len(hits) >= 1
        assert all(h.kind == "verbatim" for h in hits)


# ============================================================
# Edge cases
# ============================================================


class TestEdgeCases:
    def test_empty_query(self, adapter, aleph) -> None:
        assert dual_search("", adapter=adapter, aleph=aleph, limit=5) == []

    def test_whitespace_query(self, adapter, aleph) -> None:
        assert dual_search("   ", adapter=adapter, aleph=aleph, limit=5) == []

    def test_limit_respected(self, adapter, aleph) -> None:
        """Results never exceed limit."""
        for i in range(10):
            _write_mp(adapter, f"item {i} about testing")
            _write_aleph(aleph, f"test item {i}",
                         turn_id=f"d-{i}",
                         claims=[f"test item {i}"])

        hits = dual_search("test", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=3)
        assert len(hits) <= 3

    def test_no_results(self, adapter, aleph) -> None:
        """Query that matches nothing in either engine."""
        hits = dual_search("xyzzy nonexistent term",
                           adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)
        assert hits == []

    def test_aleph_error_degrades_gracefully(self, adapter) -> None:
        """If Aleph search throws, fall back to MemPalace-only."""
        _write_mp(adapter, "Important data about servers")

        class BrokenAleph:
            def search(self, query, *, limit=5):
                raise RuntimeError("DB corrupted")

        hits = dual_search("servers", adapter=adapter, aleph=BrokenAleph(),
                           wing="wing_test", limit=5)

        # Should still get MemPalace results
        assert len(hits) >= 1
        assert all(h.kind == "verbatim" for h in hits)


# ============================================================
# Score normalization
# ============================================================


class TestScoreNormalization:
    def test_negative_rank_maps_to_high_score(self) -> None:
        """FTS5 rank -10 → score near 1.0."""
        assert _normalize_fts5_rank(-10) > 0.9

    def test_zero_rank_maps_to_half(self) -> None:
        """FTS5 rank 0 → score 0.5."""
        assert abs(_normalize_fts5_rank(0) - 0.5) < 0.01

    def test_positive_rank_maps_to_low_score(self) -> None:
        """FTS5 rank +10 → score near 0."""
        assert _normalize_fts5_rank(10) < 0.1

    def test_extreme_negative_clamped(self) -> None:
        """Very negative rank → 1.0."""
        assert _normalize_fts5_rank(-1000) == 1.0

    def test_extreme_positive_clamped(self) -> None:
        """Very positive rank → 0.0."""
        assert _normalize_fts5_rank(1000) == 0.0


# ============================================================
# SearchHit data integrity
# ============================================================


class TestSearchHitStructure:
    def test_aleph_hit_has_metadata(self, adapter, aleph) -> None:
        """Aleph hits carry entities/claims in metadata."""
        _write_aleph(aleph, "Postgres for user service",
                     turn_id="d1",
                     entities=[{"name": "Postgres", "type": "database"}],
                     claims=["Postgres chosen"])

        hits = dual_search("Postgres", adapter=adapter, aleph=aleph, limit=5)
        aleph_hits = [h for h in hits if h.kind == "extraction"]
        assert len(aleph_hits) == 1
        assert aleph_hits[0].metadata is not None
        assert "entities" in aleph_hits[0].metadata
        assert "claims" in aleph_hits[0].metadata
        assert aleph_hits[0].extraction_id  # non-empty

    def test_mp_hit_has_no_metadata(self, adapter, aleph) -> None:
        """MemPalace hits don't carry structured metadata."""
        _write_mp(adapter, "Some raw content about testing")

        hits = dual_search("testing", adapter=adapter, aleph=aleph, limit=5)
        mp_hits = [h for h in hits if h.kind == "verbatim"]
        if mp_hits:
            assert mp_hits[0].metadata is None
            assert mp_hits[0].extraction_id == ""

    def test_ref_format(self, adapter, aleph) -> None:
        """Refs follow the expected format."""
        _write_mp(adapter, "Ref format test content")
        _write_aleph(aleph, "Ref format extraction",
                     turn_id="d-ref",
                     claims=["ref test"])

        hits = dual_search("ref format", adapter=adapter, aleph=aleph,
                           wing="wing_test", limit=5)

        for h in hits:
            if h.kind == "extraction":
                assert h.ref.startswith("aleph:")
            elif h.kind == "verbatim":
                assert h.ref.startswith("mempalace:")
