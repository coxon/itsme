"""Context-pressure hook tests — T1.17b."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from itsme.core.events import EventBus, EventType
from itsme.hooks.context_pressure import (
    DEFAULT_DISARM_DROP,
    PressureState,
    _load_state,
    _state_path,
    run_context_pressure,
)


@pytest.fixture
def bus(tmp_path: Path) -> Iterator[EventBus]:
    """Throwaway event bus rooted in pytest's tmp_path.

    Yields so the teardown closes the SQLite connection — leaked
    handles make Windows cleanups flaky and skip the close path from
    coverage.
    """
    ring = EventBus(db_path=tmp_path / "events.db")
    try:
        yield ring
    finally:
        ring.close()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Per-test state dir; cleaned up by tmp_path fixture."""
    d = tmp_path / "state"
    d.mkdir()
    return d


def _transcript(path: Path, *, chars: int) -> None:
    """Write a transcript with roughly *chars* worth of plain text."""
    msg = "x" * chars
    path.write_text(
        json.dumps({"type": "user", "message": {"content": msg}}) + "\n",
        encoding="utf-8",
    )


def _stdin(transcript_path: Path, session_id: str = "sess-1") -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "transcript_path": str(transcript_path),
            "cwd": "/tmp",
            "hook_event_name": "UserPromptSubmit",
        }
    )


def test_no_fire_below_threshold(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """Pressure under threshold: no event, no state mutation."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=400)  # 400/4 = 100 tokens

    out = run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.5,
        max_tokens=10_000,  # 100/10000 = 1% << 50%
    )

    assert out["continue"] is True
    assert "systemMessage" not in out
    assert bus.count() == 0
    # No state file written when not firing.
    assert not _state_path(state_dir, "sess-1").exists()


def test_fires_when_threshold_crossed(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """First crossing fires + writes state + populates payload metadata."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)  # 4000/4 = 1000 tokens

    out = run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.05,
        max_tokens=10_000,  # 10% > 5% threshold
    )

    assert "systemMessage" in out
    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1
    p = events[0].payload
    assert events[0].source == "hook:context-pressure"
    assert p["pressure"] == 0.1
    assert p["tokens_estimated"] == 1000
    assert p["threshold"] == 0.05
    assert p["max_tokens"] == 10_000
    # Lock transcript_ref contract: a ref to the same file must ride
    # on the payload so future consumers can re-open the source.
    assert p["transcript_ref"] == {"path": str(transcript)}

    # State persisted, disarmed.
    state = _load_state(_state_path(state_dir, "sess-1"))
    assert state.armed is False
    assert state.last_triggered == pytest.approx(0.1)


def test_debounce_blocks_second_fire(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """After firing, identical pressure must NOT re-fire."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)

    for _ in range(3):
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=0.05,
            max_tokens=10_000,
        )

    events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(events) == 1, "debounce must keep us at exactly one fire"


def test_rearms_on_significant_drop(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """Pressure drop > disarm_drop re-arms the trigger; next cross fires again."""
    transcript = tmp_path / "t.jsonl"

    # Fire once at 80%.
    _transcript(transcript, chars=32_000)  # 8000 tokens / 10000 = 80%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
        disarm_drop=0.20,  # need to drop below 60% to re-arm
    )
    assert bus.count() == 1
    assert _load_state(_state_path(state_dir, "sess-1")).armed is False

    # Drop to 50% (below 80% - 20% = 60%): re-arm tick.
    _transcript(transcript, chars=20_000)  # 5000/10000 = 50%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
        disarm_drop=0.20,
    )
    # Re-arm tick is itself a no-op event-wise but flips state.
    assert bus.count() == 1
    assert _load_state(_state_path(state_dir, "sess-1")).armed is True

    # Climb back to 70%: fires again.
    _transcript(transcript, chars=28_000)  # 7000/10000 = 70%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
        disarm_drop=0.20,
    )
    assert bus.count() == 2


def test_disabled_via_env(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """ITSME_HOOKS_DISABLED short-circuits before any work."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)

    with patch.dict(os.environ, {"ITSME_HOOKS_DISABLED": "1"}):
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=0.05,
            max_tokens=10_000,
        )

    assert bus.count() == 0


def test_missing_transcript_path_is_noop(bus: EventBus, state_dir: Path, tmp_path: Path) -> None:
    """No transcript_path in stdin → no-op (CC sometimes omits early)."""
    payload = json.dumps(
        {"session_id": "sess-1", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit"}
    )
    out = run_context_pressure(payload, bus=bus, state_dir=state_dir)
    assert out["continue"] is True
    assert bus.count() == 0


def test_env_threshold_override(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """ITSME_CTX_THRESHOLD picked up when caller doesn't pass one explicitly."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)  # 1000 tokens

    with patch.dict(os.environ, {"ITSME_CTX_THRESHOLD": "0.05", "ITSME_CTX_MAX": "10000"}):
        # Don't pass threshold/max_tokens: env should win.
        run_context_pressure(_stdin(transcript), bus=bus, state_dir=state_dir)

    assert bus.count() == 1


def test_per_session_state_isolation(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """Two sessions debounce independently — firing in A shouldn't gag B."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)

    for sess in ("alpha", "beta"):
        run_context_pressure(
            _stdin(transcript, session_id=sess),
            bus=bus,
            state_dir=state_dir,
            threshold=0.05,
            max_tokens=10_000,
        )

    # Both sessions fire on first crossing; debounce is per-session.
    assert bus.count() == 2


def test_pressure_state_round_trip() -> None:
    """PressureState.to_dict / from_dict is symmetric."""
    s = PressureState(last_triggered=0.42, armed=False)
    assert PressureState.from_dict(s.to_dict()) == s


def test_disarm_drop_default_is_used(tmp_path: Path, bus: EventBus, state_dir: Path) -> None:
    """Omitting ``disarm_drop`` ⇒ DEFAULT_DISARM_DROP (0.10) actually applies.

    Behavioural — not just a constant guard. Fires once at 80%, then:
    a 9% drop must NOT re-arm (boundary not yet reached), an 11% drop
    MUST re-arm. Catches a regression where the parameter default was
    silently wired to something else.
    """
    assert DEFAULT_DISARM_DROP == 0.10  # guard against silent change

    transcript = tmp_path / "t.jsonl"

    # Fire once at 80% — last_triggered = 0.8, disarmed.
    _transcript(transcript, chars=32_000)  # 8000 / 10000 = 80%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
        # disarm_drop omitted — must use DEFAULT_DISARM_DROP (0.10).
    )
    assert _load_state(_state_path(state_dir, "sess-1")).armed is False

    # Drop to 71% (8000-71% = 0.09 drop, just under 0.10): stay disarmed.
    _transcript(transcript, chars=28_400)  # 7100 / 10000 = 71%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
    )
    assert _load_state(_state_path(state_dir, "sess-1")).armed is False

    # Drop to 69% (0.11 drop, ≥0.10): re-arm.
    _transcript(transcript, chars=27_600)  # 6900 / 10000 = 69%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
    )
    assert _load_state(_state_path(state_dir, "sess-1")).armed is True


def test_threshold_above_one_falls_back_to_default(
    tmp_path: Path, bus: EventBus, state_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A threshold of 1.2 (user confused % vs fraction) must not silently break."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)  # 1000 tokens / 10000 = 10%

    with caplog.at_level("WARNING"):
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=1.2,  # bogus
            max_tokens=10_000,
        )

    # Default (0.70) kicks in; 10% pressure is well below, no fire.
    assert bus.count() == 0
    assert any("out of [0, 1]" in msg for msg in caplog.messages)


def test_threshold_below_zero_falls_back_to_default(
    tmp_path: Path, bus: EventBus, state_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Negative threshold should also fall back rather than fire every tick."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=40)  # tiny: 10 tokens

    with caplog.at_level("WARNING"):
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=-0.1,
            max_tokens=10_000,
        )

    assert bus.count() == 0
    assert any("out of [0, 1]" in msg for msg in caplog.messages)


def test_negative_disarm_drop_falls_back_to_default(
    tmp_path: Path, bus: EventBus, state_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Negative disarm_drop would break the re-arm guard; clamp back."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)

    with caplog.at_level("WARNING"):
        # Fire once, using the valid threshold path.
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=0.05,
            max_tokens=10_000,
            disarm_drop=-0.5,
        )

    # Warning logged; behaviour matches default (did fire since initial armed).
    assert any("disarm_drop" in msg for msg in caplog.messages)
    assert bus.count() == 1


def test_disarm_drop_above_one_falls_back_to_default(
    tmp_path: Path, bus: EventBus, state_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """disarm_drop > 1 would make re-arm impossible — clamp back."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)

    with caplog.at_level("WARNING"):
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=0.05,
            max_tokens=10_000,
            disarm_drop=1.5,  # bogus — >100%
        )

    assert any("disarm_drop" in msg and "out of [0, 1]" in msg for msg in caplog.messages)
    # Default kicks in, hook still fires once since we're armed.
    assert bus.count() == 1


def test_disarm_drop_nan_falls_back_to_default(
    tmp_path: Path, bus: EventBus, state_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """NaN disarm_drop latches the disarmed branch forever — reject."""
    transcript = tmp_path / "t.jsonl"
    _transcript(transcript, chars=4000)

    with caplog.at_level("WARNING"):
        run_context_pressure(
            _stdin(transcript),
            bus=bus,
            state_dir=state_dir,
            threshold=0.05,
            max_tokens=10_000,
            disarm_drop=float("nan"),
        )

    assert any("non-finite" in msg for msg in caplog.messages)
    assert bus.count() == 1


def test_pressure_state_from_corrupt_dict_uses_defaults() -> None:
    """A type-mangled state file (manual edit, partial write) must
    NOT propagate ValueError — the hook would otherwise stay disabled
    for the rest of the session."""
    bad = PressureState.from_dict({"last_triggered": "not-a-float", "armed": True})
    assert bad == PressureState()  # safe defaults
    bad2 = PressureState.from_dict({"last_triggered": None, "armed": False})
    assert bad2 == PressureState()


def test_pressure_state_strict_type_checks() -> None:
    """from_dict rejects bool-for-armed lookalikes and non-finite floats.

    ``bool("false")`` returns True (Python truthiness), so a permissive
    cast would silently accept string "false" as armed=True. Same for
    float("nan") — valid float but meaningless as a pressure anchor and
    poisons every ``<=`` comparison downstream.
    """
    # Non-bool armed
    assert PressureState.from_dict({"last_triggered": 0.5, "armed": "false"}) == PressureState()
    assert PressureState.from_dict({"last_triggered": 0.5, "armed": 1}) == PressureState()
    # Non-finite last_triggered
    nan_state = PressureState.from_dict({"last_triggered": float("nan"), "armed": True})
    assert nan_state == PressureState()
    inf_state = PressureState.from_dict({"last_triggered": float("inf"), "armed": True})
    assert inf_state == PressureState()
    # int is accepted and converted
    rt = PressureState.from_dict({"last_triggered": 0, "armed": True})
    assert rt == PressureState(last_triggered=0.0, armed=True)


def test_rearm_at_exact_disarm_drop_boundary(
    tmp_path: Path, bus: EventBus, state_dir: Path
) -> None:
    """A drop of *exactly* disarm_drop re-arms — boundary inclusive.

    Regression for CodeRabbit PR#7 r3: previously the disarmed branch
    used strict ``<`` so an exact 10% relief stayed disarmed for one
    extra tick, contradicting the documented ≥10% contract.
    """
    transcript = tmp_path / "t.jsonl"

    # Fire once at 80%.
    _transcript(transcript, chars=32_000)  # 8000 / 10000 = 80%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
        disarm_drop=0.20,  # boundary at 60% exact
    )
    assert bus.count() == 1

    # Drop to *exactly* 60% (80% - 20%): must re-arm.
    _transcript(transcript, chars=24_000)  # 6000 / 10000 = 60%
    run_context_pressure(
        _stdin(transcript),
        bus=bus,
        state_dir=state_dir,
        threshold=0.50,
        max_tokens=10_000,
        disarm_drop=0.20,
    )
    from itsme.hooks.context_pressure import _load_state, _state_path

    assert _load_state(_state_path(state_dir, "sess-1")).armed is True
