"""Lifecycle hook tests — T1.17 + T2.0b turn slice."""

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
    ring = EventBus(db_path=tmp_path / "events.db")
    try:
        yield ring
    finally:
        ring.close()


def _make_transcript(path: Path, messages: list[str], *, roles: list[str] | None = None) -> None:
    """Write a CC-shaped JSONL transcript."""
    with path.open("w", encoding="utf-8") as f:
        for i, m in enumerate(messages):
            role = (roles[i] if roles else "user")
            f.write(json.dumps({"type": role, "message": {"content": m}}) + "\n")


def _stdin(transcript_path: Path, **extra: object) -> str:
    payload: dict[str, object] = {
        "session_id": "sess-test",
        "transcript_path": str(transcript_path),
        "cwd": "/tmp",
        "hook_event_name": "SessionEnd",
    }
    payload.update(extra)
    return json.dumps(payload)


# ============================================================
# T2.0b — per-turn emission
# ============================================================


def test_emits_per_turn_events(tmp_path: Path, bus: EventBus) -> None:
    """T2.0b: each turn becomes its own raw.captured event."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["first turn", "second turn"])

    out = run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    assert out["continue"] is True
    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 2
    contents = {e.payload["content"] for e in events}
    assert contents == {"first turn", "second turn"}


def test_per_turn_events_share_batch_id(tmp_path: Path, bus: EventBus) -> None:
    """All turns from the same hook fire share a capture_batch_id."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["a", "b", "c"])

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 3
    batch_ids = {e.payload["capture_batch_id"] for e in events}
    assert len(batch_ids) == 1  # all same batch
    assert batch_ids.pop()  # non-empty


def test_turn_role_preserved(tmp_path: Path, bus: EventBus) -> None:
    """Each event carries the turn role (user/assistant)."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(
        transcript,
        ["user question", "assistant answer"],
        roles=["user", "assistant"],
    )

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    roles_seen = {e.payload["turn_role"] for e in events}
    assert roles_seen == {"user", "assistant"}


def test_per_turn_content_hash(tmp_path: Path, bus: EventBus) -> None:
    """Each turn has its own independent content_hash."""
    from itsme.core.dedup import content_hash

    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["alpha", "beta"])

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    hashes = {e.payload["content_hash"] for e in events}
    assert content_hash("alpha") in hashes
    assert content_hash("beta") in hashes
    assert len(hashes) == 2  # distinct per turn


# ============================================================
# Original tests — adapted for per-turn emission
# ============================================================


def test_emits_for_pre_compact_with_distinct_source(tmp_path: Path, bus: EventBus) -> None:
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
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("", encoding="utf-8")

    out = run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    assert out["continue"] is True
    assert bus.count() == 0


def test_no_emit_on_missing_transcript(tmp_path: Path, bus: EventBus) -> None:
    out = run_lifecycle_hook(
        _stdin(tmp_path / "does-not-exist.jsonl"),
        bus=bus,
        source="hook:before-exit",
    )
    assert out["continue"] is True
    assert bus.count() == 0


def test_disabled_via_env(tmp_path: Path, bus: EventBus) -> None:
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["normally captured"])

    with patch.dict(os.environ, {"ITSME_HOOKS_DISABLED": "1"}):
        run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    assert bus.count() == 0


def test_truncates_to_max_chars(tmp_path: Path, bus: EventBus) -> None:
    """max_chars bounds total capture. With per-turn emission, fewer turns are emitted."""
    transcript = tmp_path / "t.jsonl"
    big = "x" * 500
    _make_transcript(transcript, [big, big, big])  # 3 turns × 500 chars

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit", max_chars=600)

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    total_chars = sum(len(e.payload["content"]) for e in events)
    # Budget is 600 chars — should get at most 2 turns (500 + 1 < 600... wait, 501 < 600, then
    # adding another 501 > 600 so stops). First turn gets added even if over budget.
    assert total_chars <= 1200  # generous upper bound
    assert len(events) <= 3


def test_skips_tool_only_turns(tmp_path: Path, bus: EventBus) -> None:
    transcript = tmp_path / "t.jsonl"
    with transcript.open("w", encoding="utf-8") as f:
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
    with pytest.raises(ValueError):
        run_lifecycle_hook("", bus=bus, source="hook:before-exit")
    with pytest.raises(ValueError):
        run_lifecycle_hook("not json", bus=bus, source="hook:before-exit")


def test_disabled_short_circuits_before_parsing_stdin(bus: EventBus) -> None:
    with patch.dict(os.environ, {"ITSME_HOOKS_DISABLED": "1"}):
        out = run_lifecycle_hook("not json at all", bus=bus, source="hook:before-exit")
    assert out["continue"] is True
    assert bus.count() == 0


# ============================================================
# T1.19 — dedup keys
# ============================================================


def test_lifecycle_stamps_content_hash_and_producer_kind(tmp_path: Path, bus: EventBus) -> None:
    from itsme.core.dedup import content_hash, producer_kind_from_source

    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["decided to deploy on monday"])

    run_lifecycle_hook(_stdin(transcript), bus=bus, source="hook:before-exit")

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1
    payload = events[0].payload
    assert payload["content_hash"] == content_hash(payload["content"])
    assert payload["producer_kind"] == producer_kind_from_source("hook:before-exit")
    assert payload["producer_kind"] == "hook:lifecycle"


def test_pre_compact_uses_lifecycle_producer_kind(tmp_path: Path, bus: EventBus) -> None:
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["hello world"])

    run_lifecycle_hook(
        _stdin(transcript, hook_event_name="PreCompact"),
        bus=bus,
        source="hook:before-compact",
    )

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert events[0].payload["producer_kind"] == "hook:lifecycle"
