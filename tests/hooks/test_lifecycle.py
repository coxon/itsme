"""Lifecycle hook tests — T1.17."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from itsme.core.events import EventBus, EventType
from itsme.hooks.lifecycle import run_lifecycle_hook


@pytest.fixture
def bus(tmp_path: Path) -> Iterator[EventBus]:
    """Throwaway event bus rooted in pytest's tmp_path.

    Yields so the teardown can close the SQLite connection; the
    ``return``-style fixture we had before leaked handles and made the
    WAL checkpoint path untested.
    """
    ring = EventBus(db_path=tmp_path / "events.db")
    try:
        yield ring
    finally:
        ring.close()


def _make_transcript(path: Path, messages: list[str]) -> None:
    """Write a CC-shaped JSONL transcript."""
    with path.open("w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps({"type": "user", "message": {"content": m}}) + "\n")


def _stdin(transcript_path: Path, **extra: object) -> str:
    payload: dict[str, object] = {
        "session_id": "sess-test",
        "transcript_path": str(transcript_path),
        "cwd": "/tmp",
        "hook_event_name": "SessionEnd",
    }
    payload.update(extra)
    return json.dumps(payload)


def test_emits_raw_captured_for_session_end(tmp_path: Path, bus: EventBus) -> None:
    """before-exit copies transcript tail into raw.captured."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["first turn", "second turn"])

    out = run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    assert out["continue"] is True
    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1
    e = events[0]
    assert e.source == "hook:before-exit"
    assert "first turn" in e.payload["content"]
    assert "second turn" in e.payload["content"]
    assert e.payload["session_id"] == "sess-test"
    assert e.payload["transcript_ref"]["path"] == str(transcript)


def test_emits_for_pre_compact_with_distinct_source(tmp_path: Path, bus: EventBus) -> None:
    """before-compact uses its own source label so consumers can tell them apart."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["pre compact content"])

    run_lifecycle_hook(
        _stdin(transcript, hook_event_name="PreCompact"),
        bus=bus,
        source="hook:before-compact",
    )

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1
    assert events[0].source == "hook:before-compact"


def test_no_emit_on_empty_transcript(tmp_path: Path, bus: EventBus) -> None:
    """Empty transcript means nothing to salvage; skip cleanly."""
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("", encoding="utf-8")

    out = run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    assert out["continue"] is True
    assert bus.count() == 0


def test_no_emit_on_missing_transcript(tmp_path: Path, bus: EventBus) -> None:
    """Hook must not crash when CC didn't supply a transcript yet."""
    out = run_lifecycle_hook(
        _stdin(tmp_path / "does-not-exist.jsonl"),
        bus=bus,
        source="hook:before-exit",
    )
    assert out["continue"] is True
    assert bus.count() == 0


def test_disabled_via_env(tmp_path: Path, bus: EventBus) -> None:
    """``ITSME_HOOKS_DISABLED=1`` makes the hook a no-op."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["normally captured"])

    with patch.dict(os.environ, {"ITSME_HOOKS_DISABLED": "1"}):
        run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    assert bus.count() == 0


def test_truncates_to_max_chars(tmp_path: Path, bus: EventBus) -> None:
    """``max_chars`` bounds the salvage window to stop one giant event."""
    transcript = tmp_path / "t.jsonl"
    big = "x" * 500
    _make_transcript(transcript, [big, big, big])  # 3 turns × 500 chars

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit", max_chars=600)

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1
    assert len(events[0].payload["content"]) <= 600


def test_skips_tool_only_turns(tmp_path: Path, bus: EventBus) -> None:
    """Turns containing only tool_use blocks contribute no text."""
    transcript = tmp_path / "t.jsonl"
    with transcript.open("w", encoding="utf-8") as f:
        # Mixed: real text + tool-only turn
        f.write(json.dumps({"type": "user", "message": {"content": "real user msg"}}) + "\n")
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
                    },
                }
            )
            + "\n"
        )

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1
    assert events[0].payload["content"] == "real user msg"


def test_invalid_stdin_raises_value_error(bus: EventBus) -> None:
    """Empty / non-JSON stdin should raise so the CLI can log it."""
    with pytest.raises(ValueError):
        run_lifecycle_hook("", bus=bus, source="hook:before-exit")
    with pytest.raises(ValueError):
        run_lifecycle_hook("not json", bus=bus, source="hook:before-exit")
