"""End-to-end verification tests — T2.23 through T2.26.

These tests simulate the full v0.0.2 pipeline:
  hook capture → envelope strip → turn slice → intake processor →
  MemPalace + Aleph dual write → ask(mode=auto) dual-engine search.

T2.23: Full pipeline — capture → intake → dual search hits.
T2.24: Aleph miss regression — MemPalace raw catches missed entities.
T2.25: LLM degradation — no API key → MemPalace writes still work.
T2.26: Status feed shows MEMORY_ROUTED events with verdicts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.aleph.api import Aleph
from itsme.core.api import Memory
from itsme.core.events import EventBus, EventType
from itsme.core.llm import StubProvider
from itsme.core.workers.intake import IntakeProcessor


@pytest.fixture
def bus(tmp_path: Path) -> Iterator[EventBus]:
    ring = EventBus(db_path=tmp_path / "events.db")
    try:
        yield ring
    finally:
        ring.close()


@pytest.fixture
def adapter() -> InMemoryMemPalaceAdapter:
    return InMemoryMemPalaceAdapter()


@pytest.fixture
def aleph() -> Iterator[Aleph]:
    a = Aleph(":memory:")
    yield a
    a.close()


def _emit_hook_turns(
    bus: EventBus,
    turns: list[tuple[str, str]],
    batch_id: str = "batch-e2e",
) -> list:
    """Simulate hook-captured per-turn events (T2.0b output)."""
    from itsme.core.dedup import content_hash

    events = []
    for role, text in turns:
        ev = bus.emit(
            type=EventType.RAW_CAPTURED,
            source="hook:before-exit",
            payload={
                "content": text,
                "turn_role": role,
                "capture_batch_id": batch_id,
                "content_hash": content_hash(text),
                "producer_kind": "hook:lifecycle",
                "session_id": "sess-e2e",
            },
        )
        events.append(ev)
    return events


# ============================================================
# T2.23 — End-to-end pipeline
# ============================================================


class TestT223EndToEnd:
    """Capture → intake → Aleph + MemPalace → ask(mode=auto) hits."""

    def test_full_pipeline(self, bus, adapter, aleph) -> None:
        """User conversation → hook capture → intake → dual search finds it."""
        # LLM stub returns structured extractions
        llm = StubProvider(response=json.dumps([
            {
                "verdict": "keep",
                "summary": "User decided to use Postgres for the user service database",
                "entities": [
                    {"name": "Postgres", "type": "database"},
                    {"name": "user service", "type": "project"},
                ],
                "claims": [
                    "Postgres chosen for user service database",
                    "Concurrent writes requirement drove the decision",
                ],
            },
            {
                "verdict": "keep",
                "summary": "Assistant confirmed Postgres supports concurrent writes well",
                "entities": [{"name": "Postgres", "type": "database"}],
                "claims": ["Postgres handles concurrent writes effectively"],
            },
        ]))

        # Step 1: Simulate hook capture (per-turn events)
        events = _emit_hook_turns(bus, [
            ("user", "I decided to use Postgres for the user service because of concurrent writes"),
            ("assistant", "Great choice! Postgres handles concurrent writes very well with MVCC"),
        ])

        # Step 2: Run intake processor
        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=llm, wing="wing_test",
        )
        results = processor.process_batch(events)

        # Step 3: Verify dual writes
        assert len(results) == 2
        assert all(r.verdict == "keep" for r in results)
        assert all(r.drawer_id for r in results)      # MemPalace written
        assert all(r.extraction_id for r in results)   # Aleph written
        assert aleph.count() == 2                       # 2 extractions

        # Step 4: ask(mode=auto) finds it through Aleph
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("Postgres database", mode="auto")

        aleph_sources = [s for s in result.sources if s.kind == "extraction"]
        assert len(aleph_sources) >= 1
        assert any("Postgres" in s.content for s in aleph_sources)

    def test_pipeline_with_skip_turns(self, bus, adapter, aleph) -> None:
        """Skipped turns still end up in MemPalace for recall."""
        llm = StubProvider(response=json.dumps([
            {
                "verdict": "keep",
                "summary": "Planning deployment to AWS us-east-1",
                "entities": [
                    {"name": "AWS", "type": "company"},
                    {"name": "us-east-1", "type": "place"},
                ],
                "claims": ["Deploy to AWS us-east-1"],
            },
            {"verdict": "skip", "skip_reason": "procedural acknowledgment"},
            {
                "verdict": "keep",
                "summary": "Timeline set for next Friday",
                "entities": [],
                "claims": ["Deployment scheduled for next Friday"],
            },
        ]))

        events = _emit_hook_turns(bus, [
            ("user", "Let's deploy to AWS us-east-1 region"),
            ("assistant", "OK, I'll set that up"),
            ("user", "Target is next Friday"),
        ])

        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=llm, wing="wing_test",
        )
        results = processor.process_batch(events)

        assert results[0].verdict == "keep"
        assert results[1].verdict == "skip"
        assert results[2].verdict == "keep"

        # All 3 in MemPalace (including skipped)
        assert all(r.drawer_id for r in results)

        # Only 2 in Aleph (keep only)
        assert aleph.count() == 2

        # ask(mode=auto) finds both keep turns
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("AWS deployment", mode="auto")
        assert len(result.sources) >= 1


# ============================================================
# T2.24 — Aleph miss regression
# ============================================================


class TestT224AlephMissRegression:
    """LLM didn't extract an entity → MemPalace raw search catches it."""

    def test_missed_entity_found_via_mempalace(self, bus, adapter, aleph) -> None:
        """Entity mentioned in passing (not extracted by LLM) still searchable."""
        # LLM extracts the main entity but misses "Portland" (secondary mention)
        llm = StubProvider(response=json.dumps([
            {
                "verdict": "keep",
                "summary": "Meeting with the DevOps team about Kubernetes migration",
                "entities": [
                    {"name": "DevOps team", "type": "company"},
                    {"name": "Kubernetes", "type": "tool"},
                ],
                "claims": ["Kubernetes migration planned"],
                # NOTE: "Portland" is NOT extracted — LLM missed it
            },
        ]))

        events = _emit_hook_turns(bus, [
            ("user", "Had a meeting with the DevOps team in Portland about Kubernetes migration"),
        ])

        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=llm, wing="wing_test",
        )
        processor.process_batch(events)

        # Aleph does NOT find "Portland" (wasn't extracted)
        aleph_hits = aleph.search("Portland")
        assert len(aleph_hits) == 0

        # But ask(mode=auto) DOES find it via MemPalace raw
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("Portland", mode="auto")

        # MemPalace raw text contains "Portland" — dual search catches it
        mp_sources = [s for s in result.sources if s.kind == "verbatim"]
        assert len(mp_sources) >= 1
        assert any("Portland" in s.content for s in mp_sources)

    def test_completely_missed_turn_found_via_mempalace(self, bus, adapter, aleph) -> None:
        """Turn where LLM says 'skip' is still findable in MemPalace."""
        llm = StubProvider(response=json.dumps([
            {"verdict": "skip", "skip_reason": "seems procedural"},
        ]))

        # The turn actually contains a real decision, but LLM misjudged
        events = _emit_hook_turns(bus, [
            ("user", "Actually let's switch from MySQL to SQLite for the test suite"),
        ])

        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=llm, wing="wing_test",
        )
        processor.process_batch(events)

        # Nothing in Aleph (skip verdict)
        assert aleph.count() == 0

        # But raw text is in MemPalace
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("SQLite test suite", mode="auto")

        assert len(result.sources) >= 1
        assert any("SQLite" in s.content for s in result.sources)


# ============================================================
# T2.25 — LLM degradation
# ============================================================


class TestT225LLMDegradation:
    """No API key / LLM unavailable → raw writes still work."""

    def test_degraded_intake_writes_to_mempalace(self, bus, adapter, aleph) -> None:
        """With bare StubProvider (no response = degraded), MP writes succeed."""
        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=StubProvider(),  # bare = degraded mode
            wing="wing_test",
        )

        events = _emit_hook_turns(bus, [
            ("user", "Important decision about architecture"),
            ("assistant", "I recommend using microservices"),
        ])
        results = processor.process_batch(events)

        # All turns written to MemPalace
        assert all(r.drawer_id for r in results)
        # No Aleph entries (degraded)
        assert aleph.count() == 0
        # All verdicts are skip (degraded)
        assert all(r.verdict == "skip" for r in results)

    def test_degraded_ask_verbatim_still_works(self, bus, adapter, aleph) -> None:
        """ask(mode=verbatim) works without LLM — pure MemPalace search."""
        # Write directly to MemPalace (simulating prior degraded intake)
        adapter.write(
            content="Microservices architecture chosen",
            wing="wing_test",
            room="room_general",
        )

        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("microservices", mode="verbatim")

        assert result.queried_event_id
        assert len(result.sources) >= 1
        assert all(s.kind == "verbatim" for s in result.sources)

    def test_degraded_ask_auto_falls_back_to_mempalace(self, bus, adapter, aleph) -> None:
        """ask(mode=auto) with empty Aleph still returns MemPalace hits."""
        adapter.write(
            content="Redis caching layer deployed in production",
            wing="wing_test",
            room="room_general",
        )

        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        result = memory.ask("Redis production", mode="auto")

        # Should get MemPalace hits even with empty Aleph
        mp_sources = [s for s in result.sources if s.kind == "verbatim"]
        assert len(mp_sources) >= 1

    def test_memory_without_aleph_still_functions(self, bus, adapter) -> None:
        """Memory with aleph=None → auto mode degrades gracefully."""
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=None)

        adapter.write(
            content="No Aleph but still searchable",
            wing="wing_test",
            room="room_general",
        )

        result = memory.ask("searchable", mode="auto")
        assert result.queried_event_id


# ============================================================
# T2.26 — Status feed shows triaged events
# ============================================================


class TestT226StatusFeed:
    """MEMORY_ROUTED events carry verdict (skip/keep) for observability."""

    def test_status_shows_routed_events_with_verdict(self, bus, adapter, aleph) -> None:
        llm = StubProvider(response=json.dumps([
            {"verdict": "keep", "summary": "Test decision", "entities": [], "claims": ["test"]},
            {"verdict": "skip", "skip_reason": "acknowledgment"},
        ]))

        events = _emit_hook_turns(bus, [
            ("user", "Important test decision"),
            ("assistant", "Got it"),
        ])

        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=llm, wing="wing_test",
        )
        processor.process_batch(events)

        # Status should show MEMORY_ROUTED events
        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        status = memory.status(types=[EventType.MEMORY_ROUTED])

        assert status.count >= 2
        verdicts = {e.payload["verdict"] for e in status.events}
        assert "keep" in verdicts
        assert "skip" in verdicts

    def test_status_shows_memory_stored_events(self, bus, adapter, aleph) -> None:
        """MEMORY_STORED events from intake are visible in status."""
        llm = StubProvider(response=json.dumps([
            {"verdict": "keep", "summary": "Stored test", "entities": [], "claims": []},
        ]))

        events = _emit_hook_turns(bus, [("user", "stored test content")])

        processor = IntakeProcessor(
            bus=bus, adapter=adapter, aleph=aleph,
            llm=llm, wing="wing_test",
        )
        processor.process_batch(events)

        memory = Memory(bus=bus, adapter=adapter, project="test", aleph=aleph)
        status = memory.status(types=[EventType.MEMORY_STORED])

        stored_events = [e for e in status.events if e.source == "worker:intake"]
        assert len(stored_events) >= 1
