"""Tests for T4.3 — invalidation / staleness detection in intake."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC
from pathlib import Path

import pytest

from itsme.core.adapters.mempalace import InMemoryMemPalaceAdapter
from itsme.core.events import EventBus, EventType
from itsme.core.llm import StubProvider
from itsme.core.workers.intake import IntakeProcessor, _parse_intake_response


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


def _make_raw_events(bus: EventBus, turns: list[tuple[str, str]]) -> list:
    """Emit per-turn raw.captured events and return them."""
    events = []
    for role, text in turns:
        ev = bus.emit(
            type=EventType.RAW_CAPTURED,
            source="hook:before-exit",
            payload={
                "content": text,
                "turn_role": role,
                "capture_batch_id": "batch-inval",
                "content_hash": f"hash-{text[:10]}",
                "producer_kind": "hook:lifecycle",
            },
        )
        events.append(ev)
    return events


# -------------------------------------------------------- parse invalidations


class TestParseInvalidations:
    """_parse_intake_response preserves invalidations field."""

    def test_invalidations_parsed(self) -> None:
        raw = json.dumps(
            [
                {
                    "verdict": "keep",
                    "summary": "User moved from Beijing to Shanghai.",
                    "entities": [
                        {"name": "Beijing", "type": "place"},
                        {"name": "Shanghai", "type": "place"},
                    ],
                    "claims": ["User now lives in Shanghai"],
                    "invalidations": [
                        {
                            "subject": "user",
                            "predicate": "lives_in",
                            "object": "Beijing",
                        }
                    ],
                }
            ]
        )
        result = _parse_intake_response(raw, expected_count=1)
        assert len(result) == 1
        assert result[0]["invalidations"] == [
            {"subject": "user", "predicate": "lives_in", "object": "Beijing"}
        ]

    def test_missing_invalidations_defaults_empty(self) -> None:
        raw = json.dumps([{"verdict": "keep", "summary": "No inval", "entities": [], "claims": []}])
        result = _parse_intake_response(raw, expected_count=1)
        assert result[0]["invalidations"] == []

    def test_skip_entry_has_empty_invalidations(self) -> None:
        raw = json.dumps([{"verdict": "skip", "skip_reason": "procedural"}])
        result = _parse_intake_response(raw, expected_count=1)
        assert result[0]["invalidations"] == []

    def test_degraded_fallback_has_empty_invalidations(self) -> None:
        result = _parse_intake_response("not json", expected_count=2)
        for r in result:
            assert r["invalidations"] == []

    def test_truncated_pad_has_empty_invalidations(self) -> None:
        raw = json.dumps([{"verdict": "keep", "summary": "only one"}])
        result = _parse_intake_response(raw, expected_count=3)
        assert result[1]["invalidations"] == []
        assert result[2]["invalidations"] == []

    def test_invalidation_with_ended_date(self) -> None:
        raw = json.dumps(
            [
                {
                    "verdict": "keep",
                    "summary": "Left company last month.",
                    "entities": [],
                    "claims": [],
                    "invalidations": [
                        {
                            "subject": "Alice",
                            "predicate": "works_at",
                            "object": "Acme Corp",
                            "ended": "2026-04-01",
                        }
                    ],
                }
            ]
        )
        result = _parse_intake_response(raw, expected_count=1)
        inv = result[0]["invalidations"][0]
        assert inv["ended"] == "2026-04-01"

    def test_multiple_invalidations(self) -> None:
        raw = json.dumps(
            [
                {
                    "verdict": "keep",
                    "summary": "Big life change.",
                    "entities": [],
                    "claims": [],
                    "invalidations": [
                        {"subject": "user", "predicate": "lives_in", "object": "Beijing"},
                        {"subject": "user", "predicate": "works_at", "object": "OldCo"},
                    ],
                }
            ]
        )
        result = _parse_intake_response(raw, expected_count=1)
        assert len(result[0]["invalidations"]) == 2


# -------------------------------------------------------- intake processing


class TestInvalidationProcessing:
    """IntakeProcessor._apply_invalidations calls adapter.kg_invalidate."""

    def _llm_response(self, extractions: list[dict]) -> StubProvider:
        return StubProvider(response=json.dumps(extractions))

    def test_invalidation_emits_curated_event(self, bus, adapter) -> None:
        """kg_invalidate call emits a memory.curated(reason=invalidation) event."""
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "User moved from Beijing to Shanghai.",
                    "entities": [
                        {"name": "Beijing", "type": "place"},
                        {"name": "Shanghai", "type": "place"},
                    ],
                    "claims": ["User now lives in Shanghai"],
                    "invalidations": [
                        {"subject": "user", "predicate": "lives_in", "object": "Beijing"}
                    ],
                }
            ]
        )
        events = _make_raw_events(bus, [("user", "I moved from Beijing to Shanghai")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
        )
        results = processor.process_batch(events)

        # Result should carry invalidation info
        assert len(results) == 1
        r = results[0]
        assert r.verdict == "keep"
        assert len(r.invalidations) == 1
        assert r.invalidations[0]["subject"] == "user"
        assert r.invalidations[0]["predicate"] == "lives_in"
        assert r.invalidations[0]["object"] == "Beijing"

        # memory.curated event should have been emitted
        curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
        inval_events = [e for e in curated if e.payload.get("reason") == "invalidation"]
        assert len(inval_events) == 1
        payload = inval_events[0].payload
        assert payload["subject"] == "user"
        assert payload["predicate"] == "lives_in"
        assert payload["object"] == "Beijing"
        # InMemory adapter always returns False (no real KG)
        assert payload["applied"] is False

    def test_invalidations_applied_count(self, bus, adapter) -> None:
        """InMemory adapter returns False, so invalidations_applied == 0."""
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "User left old job.",
                    "entities": [],
                    "claims": [],
                    "invalidations": [
                        {"subject": "user", "predicate": "works_at", "object": "OldCo"}
                    ],
                }
            ]
        )
        events = _make_raw_events(bus, [("user", "I left OldCo")])
        processor = IntakeProcessor(bus=bus, adapter=adapter, llm=llm, wing="wing_test")
        results = processor.process_batch(events)
        # InMemory always returns False so applied count is 0
        assert results[0].invalidations_applied == 0

    def test_no_invalidations_no_event(self, bus, adapter) -> None:
        """When extraction has no invalidations, no curated event is emitted."""
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "Normal fact.",
                    "entities": [],
                    "claims": ["Sky is blue"],
                    "invalidations": [],
                }
            ]
        )
        events = _make_raw_events(bus, [("user", "The sky is blue")])
        processor = IntakeProcessor(bus=bus, adapter=adapter, llm=llm, wing="wing_test")
        processor.process_batch(events)

        curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
        inval_events = [e for e in curated if e.payload.get("reason") == "invalidation"]
        assert len(inval_events) == 0

    def test_incomplete_invalidation_skipped(self, bus, adapter) -> None:
        """Invalidation missing required fields is skipped gracefully."""
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "Incomplete inval.",
                    "entities": [],
                    "claims": [],
                    "invalidations": [
                        {"subject": "user", "predicate": "", "object": ""},
                        {"subject": "user", "predicate": "lives_in", "object": "Beijing"},
                    ],
                }
            ]
        )
        events = _make_raw_events(bus, [("user", "I moved from Beijing")])
        processor = IntakeProcessor(bus=bus, adapter=adapter, llm=llm, wing="wing_test")
        processor.process_batch(events)

        curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
        inval_events = [e for e in curated if e.payload.get("reason") == "invalidation"]
        # Only the valid one should emit an event
        assert len(inval_events) == 1
        assert inval_events[0].payload["object"] == "Beijing"

    def test_multiple_invalidations_emit_multiple_events(self, bus, adapter) -> None:
        """Each invalidation in a turn gets its own curated event."""
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "Big life change.",
                    "entities": [],
                    "claims": [],
                    "invalidations": [
                        {"subject": "user", "predicate": "lives_in", "object": "Beijing"},
                        {"subject": "user", "predicate": "works_at", "object": "OldCo"},
                    ],
                }
            ]
        )
        events = _make_raw_events(bus, [("user", "I moved to Shanghai and left OldCo")])
        processor = IntakeProcessor(bus=bus, adapter=adapter, llm=llm, wing="wing_test")
        processor.process_batch(events)

        curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
        inval_events = [e for e in curated if e.payload.get("reason") == "invalidation"]
        assert len(inval_events) == 2
        objects = {e.payload["object"] for e in inval_events}
        assert objects == {"Beijing", "OldCo"}

    def test_degraded_mode_no_invalidations(self, bus, adapter) -> None:
        """In degraded mode (no LLM), no invalidations are processed."""
        events = _make_raw_events(bus, [("user", "I moved to Shanghai")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),  # no response = degraded
            wing="wing_test",
        )
        results = processor.process_batch(events)

        assert results[0].invalidations == []
        assert results[0].invalidations_applied == 0

        curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
        inval_events = [e for e in curated if e.payload.get("reason") == "invalidation"]
        assert len(inval_events) == 0

    def test_intake_result_carries_invalidations(self, bus, adapter) -> None:
        """IntakeResult dataclass includes invalidations list."""
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "Moved cities.",
                    "entities": [],
                    "claims": [],
                    "invalidations": [
                        {
                            "subject": "user",
                            "predicate": "lives_in",
                            "object": "Beijing",
                            "ended": "2026-04-15",
                        }
                    ],
                }
            ]
        )
        events = _make_raw_events(bus, [("user", "I moved from Beijing last month")])
        processor = IntakeProcessor(bus=bus, adapter=adapter, llm=llm, wing="wing_test")
        results = processor.process_batch(events)

        r = results[0]
        assert len(r.invalidations) == 1
        assert r.invalidations[0]["ended"] == "2026-04-15"


# -------------------------------------------------------- status feed rendering


class TestInvalidationStatusRendering:
    """Status feed renders invalidation events correctly."""

    def test_render_invalidation_event(self) -> None:
        from itsme.mcp.tools.status import _render_payload

        tag, summary = _render_payload(
            "memory.curated",
            "worker:intake:invalidation",
            {
                "reason": "invalidation",
                "subject": "user",
                "predicate": "lives_in",
                "object": "Beijing",
                "ended": "2026-04-15",
                "applied": True,
            },
        )
        assert tag == "⏳ inval"
        assert "✓" in summary
        assert "user.lives_in→Beijing" in summary

    def test_render_invalidation_not_applied(self) -> None:
        from itsme.mcp.tools.status import _render_payload

        tag, summary = _render_payload(
            "memory.curated",
            "worker:intake:invalidation",
            {
                "reason": "invalidation",
                "subject": "user",
                "predicate": "lives_in",
                "object": "Beijing",
                "applied": False,
            },
        )
        assert tag == "⏳ inval"
        assert "○" in summary  # not applied marker

    def test_summary_counts_inval(self) -> None:
        from datetime import datetime

        from itsme.core.api import StatusEvent
        from itsme.mcp.tools.status import _feed_summary_line

        events = [
            StatusEvent(
                id="01TESTID" + "A" * 18,
                ts=datetime.now(tz=UTC),
                type="memory.curated",
                source="worker:intake:invalidation",
                payload={"reason": "invalidation", "applied": True},
            ),
            StatusEvent(
                id="01TESTID" + "B" * 18,
                ts=datetime.now(tz=UTC),
                type="memory.curated",
                source="worker:intake:invalidation",
                payload={"reason": "invalidation", "applied": False},
            ),
        ]
        line = _feed_summary_line(events)
        assert "2 inval" in line
