"""Tests for core.workers.intake — T2.0d intake processor."""

from __future__ import annotations

import json
from collections.abc import Iterator
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
                "capture_batch_id": "batch-001",
                "content_hash": f"hash-{text[:10]}",
                "producer_kind": "hook:lifecycle",
            },
        )
        events.append(ev)
    return events


# -------------------------------------------------------- degraded mode (no LLM)


class TestDegradedMode:
    def test_writes_all_turns_to_mempalace(self, bus, adapter) -> None:
        """Even without LLM, all turns go to MemPalace."""
        events = _make_raw_events(
            bus,
            [
                ("user", "I decided to use Postgres"),
                ("assistant", "Good choice for concurrent writes"),
            ],
        )
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),
            wing="wing_test",
        )
        results = processor.process_batch(events)

        assert len(results) == 2
        assert all(r.drawer_id for r in results)  # all written to MP
        assert all(r.verdict == "skip" for r in results)  # no LLM = skip
        assert all(r.skip_reason == "llm_unavailable" for r in results)

    def test_no_wiki_pages_in_degraded(self, bus, adapter) -> None:
        events = _make_raw_events(bus, [("user", "test content")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),
            wing="wing_test",
        )
        processor.process_batch(events)
        # No Aleph, no crash — just MemPalace writes


# -------------------------------------------------------- with LLM


class TestWithLLM:
    def _llm_response(self, extractions: list[dict]) -> StubProvider:
        return StubProvider(response=json.dumps(extractions))

    def test_keep_turn_writes_mempalace(self, bus, adapter) -> None:
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "User chose Postgres for concurrent writes",
                    "entities": [{"name": "Postgres", "type": "database"}],
                    "claims": ["Postgres chosen for concurrent writes"],
                },
            ]
        )
        events = _make_raw_events(bus, [("user", "I decided to use Postgres")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
        )
        results = processor.process_batch(events)

        assert len(results) == 1
        r = results[0]
        assert r.verdict == "keep"
        assert r.drawer_id  # MP written
        assert r.summary == "User chose Postgres for concurrent writes"
        assert len(r.entities) == 1
        assert r.entities[0]["name"] == "Postgres"

    def test_skip_turn_writes_mempalace_only(self, bus, adapter) -> None:
        llm = self._llm_response(
            [
                {"verdict": "skip", "skip_reason": "procedural acknowledgment"},
            ]
        )
        events = _make_raw_events(bus, [("assistant", "OK, let me check that.")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
        )
        results = processor.process_batch(events)

        assert results[0].verdict == "skip"
        assert results[0].drawer_id  # MP written (全量入库)

    def test_mixed_batch(self, bus, adapter) -> None:
        llm = self._llm_response(
            [
                {
                    "verdict": "keep",
                    "summary": "Decided on Postgres",
                    "entities": [{"name": "Postgres", "type": "database"}],
                    "claims": ["Chose Postgres"],
                },
                {"verdict": "skip", "skip_reason": "acknowledgment"},
                {
                    "verdict": "keep",
                    "summary": "Planning Seattle trip",
                    "entities": [{"name": "Seattle", "type": "place"}],
                    "claims": ["Trip to Seattle next month"],
                },
            ]
        )
        events = _make_raw_events(
            bus,
            [
                ("user", "Let's use Postgres"),
                ("assistant", "OK"),
                ("user", "Also I'm going to Seattle next month"),
            ],
        )
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
        )
        results = processor.process_batch(events)

        assert len(results) == 3
        assert results[0].verdict == "keep"
        assert results[1].verdict == "skip"
        assert results[2].verdict == "keep"
        # All 3 in MemPalace
        assert all(r.drawer_id for r in results)

    def test_emits_memory_stored_events(self, bus, adapter) -> None:
        llm = self._llm_response(
            [
                {"verdict": "keep", "summary": "Test", "entities": [], "claims": []},
            ]
        )
        events = _make_raw_events(bus, [("user", "test")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
        )
        processor.process_batch(events)

        stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
        assert len(stored) >= 1
        assert stored[0].source == "worker:intake"

    def test_emits_routed_events_with_verdict(self, bus, adapter) -> None:
        llm = self._llm_response(
            [
                {"verdict": "keep", "summary": "Test", "entities": [], "claims": []},
            ]
        )
        events = _make_raw_events(bus, [("user", "test")])
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=llm,
            wing="wing_test",
        )
        processor.process_batch(events)

        routed = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
        assert len(routed) >= 1
        assert routed[0].payload["verdict"] == "keep"

    def test_empty_batch(self, bus, adapter) -> None:
        processor = IntakeProcessor(
            bus=bus,
            adapter=adapter,
            llm=StubProvider(),
            wing="wing_test",
        )
        assert processor.process_batch([]) == []


# -------------------------------------------------------- response parsing


class TestParseIntakeResponse:
    def test_valid_json_array(self) -> None:
        raw = json.dumps(
            [
                {"verdict": "keep", "summary": "X", "entities": [], "claims": []},
                {"verdict": "skip", "skip_reason": "low info"},
            ]
        )
        result = _parse_intake_response(raw, expected_count=2)
        assert len(result) == 2
        assert result[0]["verdict"] == "keep"
        assert result[1]["verdict"] == "skip"

    def test_markdown_fenced(self) -> None:
        raw = '```json\n[{"verdict": "keep", "summary": "Y", "entities": [], "claims": []}]\n```'
        result = _parse_intake_response(raw, expected_count=1)
        assert result[0]["verdict"] == "keep"

    def test_truncated_pads(self) -> None:
        raw = json.dumps([{"verdict": "keep", "summary": "only one"}])
        result = _parse_intake_response(raw, expected_count=3)
        assert len(result) == 3
        assert result[0]["verdict"] == "keep"
        assert result[1]["verdict"] == "skip"
        assert result[1]["skip_reason"] == "llm_truncated"

    def test_extra_entries_truncated(self) -> None:
        raw = json.dumps(
            [
                {"verdict": "keep"},
                {"verdict": "skip"},
                {"verdict": "keep"},
            ]
        )
        result = _parse_intake_response(raw, expected_count=2)
        assert len(result) == 2

    def test_non_json_degrades(self) -> None:
        result = _parse_intake_response("not json at all", expected_count=2)
        assert len(result) == 2
        assert all(r["verdict"] == "skip" for r in result)

    def test_non_array_degrades(self) -> None:
        result = _parse_intake_response('{"verdict": "keep"}', expected_count=1)
        assert len(result) == 1
        assert result[0]["verdict"] == "skip"

    def test_malformed_entry_skipped(self) -> None:
        raw = json.dumps([42, {"verdict": "keep", "summary": "OK"}])
        result = _parse_intake_response(raw, expected_count=2)
        assert result[0]["verdict"] == "skip"
        assert result[0]["skip_reason"] == "malformed_entry"
        assert result[1]["verdict"] == "keep"
