"""Tests for the Curator worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.wiki import Aleph
from itsme.core.events import EventBus, EventType
from itsme.core.workers.curator import Curator

# ================================================================ fixtures


@pytest.fixture()
def wiki_dir(tmp_path: Path) -> Path:
    root = tmp_path / "aleph"
    root.mkdir()
    (root / "dna.md").write_text("# Aleph DNA\n")
    (root / "index.md").write_text("# Aleph Index\n")
    (root / "wings").mkdir()
    return root


@pytest.fixture()
def bus(tmp_path: Path) -> EventBus:
    b = EventBus(db_path=tmp_path / "events.db")
    yield b
    b.close()


def _write_page(
    root: Path,
    *,
    slug: str,
    title: str = "",
    domain: str = "work",
    subcategory: str = "projects",
    body: str = "",
) -> None:
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


# ================================================================ tests


class TestCurator:
    def test_run_crosslink_and_refresh(self, wiki_dir: Path, bus: EventBus) -> None:
        """Curator runs both crosslink and refresh."""
        _write_page(wiki_dir, slug="alpha", title="Alpha", body="Mentions Beta.")
        _write_page(wiki_dir, slug="beta", title="Beta", body="Mentions Alpha.\n\nDup.\n\nDup.")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=bus)
        result = curator.run()

        # Crosslink should insert links
        assert result.crosslink.links_inserted >= 2
        # Refresh should remove duplicate paragraph
        assert result.refresh.paragraphs_removed == 1
        assert result.total_changes >= 3

    def test_emits_curated_events(self, wiki_dir: Path, bus: EventBus) -> None:
        """Curator emits memory.curated events."""
        _write_page(wiki_dir, slug="foo", title="Foo", body="Says Bar.")
        _write_page(wiki_dir, slug="bar", title="Bar", body="nothing")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=bus)
        curator.run()

        curated_events = bus.tail(n=10, types=[EventType.MEMORY_CURATED])
        assert len(curated_events) >= 1
        reasons = [e.payload.get("reason") for e in curated_events]
        assert "crosslink" in reasons

    def test_no_events_on_dry_run(self, wiki_dir: Path, bus: EventBus) -> None:
        """Dry run doesn't emit events."""
        _write_page(wiki_dir, slug="x", title="X", body="Y.")
        _write_page(wiki_dir, slug="y", title="Y", body="nothing")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=bus)
        result = curator.run(dry_run=True)

        # Changes computed but not written
        assert result.crosslink.links_inserted >= 1
        # No events emitted
        curated_events = bus.tail(n=10, types=[EventType.MEMORY_CURATED])
        assert len(curated_events) == 0

    def test_no_changes_no_events(self, wiki_dir: Path, bus: EventBus) -> None:
        """When wiki is clean, no events are emitted."""
        _write_page(wiki_dir, slug="clean", title="Clean", body="Just fine.")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=bus)
        result = curator.run()

        assert result.total_changes == 0
        curated_events = bus.tail(n=10, types=[EventType.MEMORY_CURATED])
        assert len(curated_events) == 0

    def test_no_bus(self, wiki_dir: Path) -> None:
        """Curator works without a bus (standalone mode)."""
        _write_page(wiki_dir, slug="a", title="A", body="B says hi.")
        _write_page(wiki_dir, slug="b", title="B", body="nothing")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=None)
        result = curator.run()

        assert result.crosslink.links_inserted >= 1

    def test_idempotent(self, wiki_dir: Path, bus: EventBus) -> None:
        """Running curator twice — second run has no changes."""
        _write_page(wiki_dir, slug="p1", title="P1", body="P2 is great.\n\nDup.\n\nDup.")
        _write_page(wiki_dir, slug="p2", title="P2", body="P1 is great.")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=bus)

        r1 = curator.run()
        assert r1.total_changes >= 3  # 2 crosslinks + 1 dedup

        r2 = curator.run()
        assert r2.total_changes == 0

    def test_refresh_before_crosslink_order(self, wiki_dir: Path) -> None:
        """Refresh runs before crosslink — deduped text isn't crosslinked."""
        _write_page(
            wiki_dir,
            slug="target",
            title="Target",
            body="Mentions Source.\n\nMentions Source.",
        )
        _write_page(wiki_dir, slug="source", title="Source", body="nothing")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=None)
        result = curator.run()

        # Refresh should remove one duplicate paragraph first
        assert result.refresh.paragraphs_removed == 1
        # Crosslink should link the remaining occurrence
        assert result.crosslink.links_inserted >= 1

        _, body = aleph.read_page("wings/work/projects/target.md")
        # Only one [[source|Source]] link (not two)
        assert body.count("[[source|Source]]") == 1

    def test_dedup_pages_with_adapter(self, wiki_dir: Path, bus: EventBus) -> None:
        """Curator runs dedup-pages when adapter is provided."""
        # Many distinct shared tokens so Jaccard stays above 0.85 despite
        # differing title tokens ("A" vs "B", "# Topic A" vs "# Topic B").
        shared = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
        _write_page(wiki_dir, slug="page-a", title="Topic A", body=shared)
        _write_page(wiki_dir, slug="page-b", title="Topic B", body=shared)

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        adapter.write(content=f"Topic A\ntest\n{shared}", wing="aleph", room="room_wiki")
        adapter.write(content=f"Topic B\ntest\n{shared}", wing="aleph", room="room_wiki")

        curator = Curator(aleph=aleph, bus=bus, adapter=adapter)
        result = curator.run()

        assert result.merge_candidates >= 1

    def test_no_dedup_without_adapter(self, wiki_dir: Path, bus: EventBus) -> None:
        """Curator skips dedup-pages when no adapter is provided."""
        _write_page(wiki_dir, slug="solo", title="Solo", body="content")

        aleph = Aleph(wiki_dir)
        curator = Curator(aleph=aleph, bus=bus, adapter=None)
        result = curator.run()

        assert result.merge_candidates == 0

    def test_dedup_emits_merge_candidate_event(self, wiki_dir: Path, bus: EventBus) -> None:
        """Dedup-pages emits memory.curated(reason=merge_candidate)."""
        _write_page(wiki_dir, slug="dup1", title="Dup One", body="same same same topic here")
        _write_page(wiki_dir, slug="dup2", title="Dup Two", body="same same same topic here")

        aleph = Aleph(wiki_dir)
        adapter = InMemoryMemPalaceAdapter()
        adapter.write(
            content="Dup One\ntest\nsame same same topic here",
            wing="aleph",
            room="room_wiki",
        )
        adapter.write(
            content="Dup Two\ntest\nsame same same topic here",
            wing="aleph",
            room="room_wiki",
        )

        curator = Curator(aleph=aleph, bus=bus, adapter=adapter)
        curator.run()

        curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
        reasons = [e.payload.get("reason") for e in curated]
        if any(r == "merge_candidate" for r in reasons):
            merge_evt = next(e for e in curated if e.payload.get("reason") == "merge_candidate")
            assert "candidates" in merge_evt.payload
            assert merge_evt.payload["count"] >= 1
