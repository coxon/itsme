"""Tests for core.aleph.store.index — T2.1 extraction index."""

from __future__ import annotations

import pytest

from itsme.core.aleph.store.index import Extraction, ExtractionHit, ExtractionIndex


@pytest.fixture
def index() -> ExtractionIndex:
    idx = ExtractionIndex(":memory:")
    yield idx
    idx.close()


# ---------------------------------------------------------------- write


class TestWrite:
    def test_basic_write(self, index: ExtractionIndex) -> None:
        ext = index.write(
            turn_id="drawer_001",
            raw_event_id="evt_001",
            summary="User chose Postgres over SQLite",
            entities=[
                {"name": "Postgres", "type": "database", "role": "chosen"},
                {"name": "SQLite", "type": "database", "role": "rejected"},
            ],
            claims=["Postgres chosen for concurrent write support"],
            source="hook:before-exit",
        )
        assert isinstance(ext, Extraction)
        assert ext.turn_id == "drawer_001"
        assert ext.summary == "User chose Postgres over SQLite"
        assert len(ext.entities) == 2
        assert ext.entities[0]["name"] == "Postgres"
        assert len(ext.claims) == 1
        assert ext.id  # ULID generated

    def test_count_increments(self, index: ExtractionIndex) -> None:
        assert index.count() == 0
        index.write(
            turn_id="d1",
            raw_event_id="e1",
            summary="s1",
            entities=[],
            claims=[],
        )
        assert index.count() == 1
        index.write(
            turn_id="d2",
            raw_event_id="e2",
            summary="s2",
            entities=[],
            claims=[],
        )
        assert index.count() == 2

    def test_empty_entities_and_claims(self, index: ExtractionIndex) -> None:
        ext = index.write(
            turn_id="d1",
            raw_event_id="e1",
            summary="Just a note",
            entities=[],
            claims=[],
        )
        assert ext.entities == []
        assert ext.claims == []

    def test_cjk_content(self, index: ExtractionIndex) -> None:
        ext = index.write(
            turn_id="d1",
            raw_event_id="e1",
            summary="用户决定使用 Postgres",
            entities=[{"name": "数据库", "type": "concept"}],
            claims=["Postgres 被选择因为并发写支持"],
        )
        assert "Postgres" in ext.summary
        assert ext.entities[0]["name"] == "数据库"


# ---------------------------------------------------------------- search


class TestSearch:
    def _seed(self, index: ExtractionIndex) -> None:
        index.write(
            turn_id="d1",
            raw_event_id="e1",
            summary="Decided to use Postgres for the worker pool database",
            entities=[
                {"name": "Postgres", "type": "database"},
                {"name": "worker pool", "type": "component"},
            ],
            claims=["Postgres chosen for concurrent writes"],
        )
        index.write(
            turn_id="d2",
            raw_event_id="e2",
            summary="Apollo defense tech company Warfighter OS drones",
            entities=[
                {"name": "Apollo", "type": "company"},
                {"name": "Warfighter OS", "type": "product"},
            ],
            claims=["Apollo builds drone operating system for disconnected battlefields"],
        )
        index.write(
            turn_id="d3",
            raw_event_id="e3",
            summary="Had dinner with 老王 who mentioned DuckDB for OLAP",
            entities=[
                {"name": "老王", "type": "person"},
                {"name": "DuckDB", "type": "database"},
            ],
            claims=["老王 team uses DuckDB", "DuckDB simpler than ClickHouse to deploy"],
        )

    def test_search_entity_name(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("Postgres")
        assert len(hits) >= 1
        assert any(h.extraction.turn_id == "d1" for h in hits)

    def test_search_summary_keyword(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("Apollo drones")
        assert len(hits) >= 1
        assert any(h.extraction.turn_id == "d2" for h in hits)

    def test_search_claim_content(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("DuckDB ClickHouse")
        assert len(hits) >= 1
        assert any(h.extraction.turn_id == "d3" for h in hits)

    def test_search_cjk(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("老王")
        assert len(hits) >= 1
        assert any(h.extraction.turn_id == "d3" for h in hits)

    def test_search_no_match(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("zxqvbn unicorn")
        assert hits == []

    def test_search_empty_query(self, index: ExtractionIndex) -> None:
        self._seed(index)
        assert index.search("") == []
        assert index.search("   ") == []

    def test_search_limit(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("database", limit=1)
        assert len(hits) <= 1

    def test_search_returns_extraction_hit(self, index: ExtractionIndex) -> None:
        self._seed(index)
        hits = index.search("Postgres")
        assert all(isinstance(h, ExtractionHit) for h in hits)
        assert all(isinstance(h.extraction, Extraction) for h in hits)
        assert all(isinstance(h.rank, float) for h in hits)

    def test_search_special_chars_degrade(self, index: ExtractionIndex) -> None:
        """Queries with special FTS chars should not crash."""
        self._seed(index)
        # These should not raise, may return empty
        index.search("C++")
        index.search("user@email.com")
        index.search("what's up?")


# -------------------------------------------------------- persistence (file)


class TestPersistence:
    def test_survives_reopen(self, tmp_path) -> None:
        db = tmp_path / "aleph.db"
        idx1 = ExtractionIndex(db)
        idx1.write(
            turn_id="d1",
            raw_event_id="e1",
            summary="Postgres decision",
            entities=[],
            claims=[],
        )
        idx1.close()

        idx2 = ExtractionIndex(db)
        assert idx2.count() == 1
        hits = idx2.search("Postgres")
        assert len(hits) == 1
        idx2.close()
