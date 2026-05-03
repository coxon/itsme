"""Context-pressure hook — T1.17b (proactive salvage).

Fires on every ``UserPromptSubmit`` / ``PostToolUse`` event, which means
**many** times per session. To keep signal high and noise low we use a
two-state debounce:

* **armed** → pressure below threshold, next crossing fires a capture.
* **disarmed** → a capture was just emitted; we wait until pressure
  drops back *below* ``last_triggered - disarm_drop`` before re-arming.

This is a Schmitt trigger: a shallow dip (a few user messages after a
capture) won't cause a second capture, but a real relief (after
``/compact`` or a new topic) will re-arm the hook.

State persists across hook invocations via a per-session JSON file at
``~/.itsme/state/pressure-<session_id>.json``. Session-scoped so
different CC sessions don't interfere; survives process restarts within
the same session.

v0.0.1 simplifications:

* Token estimate is ``chars // 4``. A tokenizer dependency is not worth
  its weight at debounce-decision fidelity.
* ``max_tokens`` defaults to 200_000 (Claude Sonnet 4 default window).
  Override via ``$ITSME_CTX_MAX`` for other models.
* Threshold defaults to 0.70 (14K tokens headroom before ``PreCompact``
  usually fires). Override via ``$ITSME_CTX_THRESHOLD``.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from itsme.core.dedup import content_hash, producer_kind_from_source
from itsme.core.events import EventBus, EventType
from itsme.hooks import _common

_logger = logging.getLogger(__name__)

#: Default fraction of ``max_tokens`` at which we fire.
DEFAULT_THRESHOLD: float = 0.70

#: Default assumed context window (Claude Sonnet 4 default).
DEFAULT_MAX_TOKENS: int = 200_000

#: Default relief required to re-arm after firing (fraction of max).
DEFAULT_DISARM_DROP: float = 0.10

#: Snapshot size when firing — matches lifecycle hooks for consistency.
DEFAULT_SNAPSHOT_CHARS: int = 10_000


@dataclass
class PressureState:
    """Debounce state persisted per session.

    Attributes:
        last_triggered: The pressure value at which the last capture
            fired (0.0 if never). Used as the re-arm anchor.
        armed: True means the next cross of *threshold* fires; False
            means we're waiting for pressure to drop by *disarm_drop*.
    """

    last_triggered: float = 0.0
    armed: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the JSON form used by the state file."""
        return {"last_triggered": self.last_triggered, "armed": self.armed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PressureState:
        """Parse from JSON; missing / malformed values default defensively.

        State files are tiny, infrequently written, and persist across
        process restarts — a corrupt one (wrong types after manual edit
        or partial write) must not disable the hook for the rest of the
        session. Validation is strict about *types* rather than
        permissive cast: ``bool("false")`` returns True, and
        ``float("nan")`` is a valid float but meaningless as a
        pressure anchor, so we reject both explicitly.
        """
        raw_last = data.get("last_triggered", 0.0)
        raw_armed = data.get("armed", True)
        if not isinstance(raw_armed, bool):
            return cls()
        if isinstance(raw_last, bool):  # bool is subclass of int — reject
            return cls()
        if not isinstance(raw_last, int | float):
            return cls()
        if not math.isfinite(raw_last):
            return cls()
        return cls(last_triggered=float(raw_last), armed=raw_armed)


# ------------------------------------------------------------- state IO


def _state_path(state_dir: Path, session_id: str) -> Path:
    """Per-session state file; session_id comes straight from CC."""
    safe = _safe_session_id(session_id)
    return state_dir / f"pressure-{safe}.json"


def _safe_session_id(session_id: str) -> str:
    """Strip anything weird. CC session_ids look like ULIDs but we
    treat them as opaque strings, so guard against path separators."""
    return "".join(c for c in session_id if c.isalnum() or c in "-_")


def _load_state(path: Path) -> PressureState:
    """Load state or return defaults if file missing / corrupt."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return PressureState()
    if not isinstance(data, dict):
        return PressureState()
    return PressureState.from_dict(data)


def _save_state(path: Path, state: PressureState) -> None:
    """Persist state. Errors are logged but swallowed — losing the
    state file at worst causes an extra capture next time, not a crash.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.to_dict()), encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        _logger.warning("itsme: could not persist pressure state: %s", exc)


# --------------------------------------------------------------- env


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("itsme: ignoring bad %s=%r, using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning("itsme: ignoring bad %s=%r, using %s", name, raw, default)
        return default


# ------------------------------------------------------------- main


def run_context_pressure(
    stdin_text: str,
    *,
    bus: EventBus,
    state_dir: Path,
    threshold: float | None = None,
    max_tokens: int | None = None,
    disarm_drop: float = DEFAULT_DISARM_DROP,
    snapshot_chars: int = DEFAULT_SNAPSHOT_CHARS,
) -> dict[str, Any]:
    """Sample transcript pressure, emit ``raw.captured`` when crossing.

    Args:
        stdin_text: The CC hook JSON on stdin.
        bus: Open :class:`EventBus`.
        state_dir: Where per-session debounce state lives.
        threshold: Fraction of *max_tokens* that triggers a capture;
            defaults to ``$ITSME_CTX_THRESHOLD`` or 0.70.
        max_tokens: Assumed context window; defaults to
            ``$ITSME_CTX_MAX`` or 200_000.
        disarm_drop: How much pressure must drop below the last trigger
            before we re-arm.
        snapshot_chars: Upper bound on saved text when firing.

    Returns:
        Standard CC hook output. ``systemMessage`` set on fire so the
        operator sees "itsme captured at 72%" in the transcript.
    """
    # Disabled-first: a no-op hook must not raise on malformed stdin.
    # Mirrors lifecycle.run_lifecycle_hook for consistency across hooks.
    if _common.hooks_disabled():
        return _common.ok_output()
    payload_in = _common.load_hook_input(stdin_text)

    transcript_path_raw = payload_in.get("transcript_path")
    session_id_raw = payload_in.get("session_id")
    if not isinstance(transcript_path_raw, str) or not transcript_path_raw:
        return _common.ok_output()
    if not isinstance(session_id_raw, str) or not session_id_raw:
        return _common.ok_output()

    resolved_threshold = (
        threshold if threshold is not None else _env_float("ITSME_CTX_THRESHOLD", DEFAULT_THRESHOLD)
    )
    resolved_max = (
        max_tokens if max_tokens is not None else _env_int("ITSME_CTX_MAX", DEFAULT_MAX_TOKENS)
    )
    # Range-validate before we act. Bad env values — e.g. "70" (meant
    # as %), "1.2" (>100%), "-1" — would otherwise either fire every
    # tick or never fire, silently breaking the debounce contract.
    # Fall back to defaults with a warning instead of failing loudly;
    # this is a hook, not an MCP call.
    if not 0.0 <= resolved_threshold <= 1.0:
        _logger.warning(
            "itsme: ITSME_CTX_THRESHOLD=%r out of [0, 1]; falling back to %s",
            resolved_threshold,
            DEFAULT_THRESHOLD,
        )
        resolved_threshold = DEFAULT_THRESHOLD
    # disarm_drop must be a sane fraction too: ``>1`` would make re-arm
    # impossible (pressure can never drop below zero), ``NaN`` breaks
    # every ``<=`` comparison and latches the hook forever.
    if not math.isfinite(disarm_drop) or not 0.0 <= disarm_drop <= 1.0:
        _logger.warning(
            "itsme: disarm_drop=%r out of [0, 1] or non-finite; falling back to %s",
            disarm_drop,
            DEFAULT_DISARM_DROP,
        )
        disarm_drop = DEFAULT_DISARM_DROP
    if resolved_max <= 0:
        return _common.ok_output()

    transcript_path = Path(transcript_path_raw)
    full_text = _common.read_transcript_full(transcript_path)
    tokens = _common.estimate_tokens(full_text)
    pressure = tokens / resolved_max

    state_file = _state_path(state_dir, session_id_raw)
    state = _load_state(state_file)

    # Disarmed branch: only re-arm, never fire.
    if not state.armed:
        # ``<=`` so a drop of *exactly* ``disarm_drop`` re-arms — matches
        # the documented "drop ≥ disarm_drop" contract. Strict ``<``
        # would miss the boundary and leave the hook silently disarmed
        # one tick longer than intended.
        if pressure <= state.last_triggered - disarm_drop:
            state.armed = True
            _save_state(state_file, state)
        return _common.ok_output()

    # Armed branch: fire only if threshold is crossed.
    if pressure < resolved_threshold:
        return _common.ok_output()

    snapshot = _common.read_transcript_tail(transcript_path, max_chars=snapshot_chars)
    if not snapshot.strip():
        # Pressure crossed but the file yielded nothing readable; skip
        # without disarming so the next tick retries.
        return _common.ok_output()

    bus.emit(
        type=EventType.RAW_CAPTURED,
        source="hook:context-pressure",
        payload={
            "content": snapshot,
            "kind": None,
            # T1.19: cross-producer dedup keys (see core/dedup.py).
            "content_hash": content_hash(snapshot),
            "producer_kind": producer_kind_from_source("hook:context-pressure"),
            "pressure": round(pressure, 3),
            "tokens_estimated": tokens,
            "threshold": resolved_threshold,
            "max_tokens": resolved_max,
            "hook_event": payload_in.get("hook_event_name"),
            "session_id": session_id_raw,
            "transcript_ref": {"path": transcript_path_raw},
            "cwd": payload_in.get("cwd"),
        },
    )

    state.last_triggered = pressure
    state.armed = False
    _save_state(state_file, state)

    return _common.ok_output(system_message=f"itsme: captured at {pressure:.0%} context pressure")
