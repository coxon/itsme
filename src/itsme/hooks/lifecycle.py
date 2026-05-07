"""Lifecycle hooks — T1.17 + T2.0b turn slice.

Handles CC's ``SessionEnd`` and ``PreCompact`` events. Both read a
bounded tail of the session transcript and emit **per-turn**
``raw.captured`` events (T2.0b) — each user or assistant turn becomes
its own event with an independent ``content_hash``.

All turns from the same hook fire share a ``capture_batch_id`` so the
downstream intake worker can batch them into a single LLM call.

For v0.0.1 this emitted one big blob per hook fire. v0.0.2 (T2.0b)
splits by turn so MemPalace stores ~200-500 token drawers instead of
a ~2000 token blob, and per-turn Aleph extraction becomes possible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ulid import ULID

from itsme.core.dedup import content_hash, producer_kind_from_source
from itsme.core.events import EventBus, EventType
from itsme.hooks import _common

#: Default salvage window — 10K chars ≈ 2.5K tokens, large enough to
#: preserve recent decisions/context but small enough to not blow up
#: the 500-slot ring with one huge event.
DEFAULT_SNAPSHOT_CHARS: int = 10_000


def run_lifecycle_hook(
    stdin_text: str,
    *,
    bus: EventBus,
    source: str,
    max_chars: int = DEFAULT_SNAPSHOT_CHARS,
) -> dict[str, Any]:
    """Emit per-turn ``raw.captured`` events from the transcript tail.

    Args:
        stdin_text: Raw JSON the CC runtime piped to the hook process.
        bus: Open :class:`EventBus` (caller owns lifecycle).
        source: Event source label — ``"hook:before-exit"`` for
            SessionEnd, ``"hook:before-compact"`` for PreCompact.
        max_chars: Upper bound on total captured text (default 10K).

    Returns:
        The CC hook output dict (``continue=True`` always — hooks must
        not block the IDE on capture failures).
    """
    if _common.hooks_disabled():
        return _common.ok_output()
    payload_in = _common.load_hook_input(stdin_text)

    transcript_path_raw = payload_in.get("transcript_path")
    if not isinstance(transcript_path_raw, str) or not transcript_path_raw:
        return _common.ok_output()

    turns = _common.read_transcript_tail_turns(
        Path(transcript_path_raw),
        max_chars=max_chars,
    )
    if not turns:
        return _common.ok_output()

    # All turns from this hook fire share a batch id so the intake
    # worker can group them into a single LLM call.
    batch_id = str(ULID())
    producer = producer_kind_from_source(source)
    session_id = payload_in.get("session_id")

    for turn in turns:
        bus.emit(
            type=EventType.RAW_CAPTURED,
            source=source,
            payload={
                "content": turn.text,
                "kind": None,
                "turn_role": turn.role,
                "capture_batch_id": batch_id,
                "content_hash": content_hash(turn.text),
                "producer_kind": producer,
                "hook_event": payload_in.get("hook_event_name"),
                "session_id": session_id,
                "transcript_ref": {"path": transcript_path_raw},
                "cwd": payload_in.get("cwd"),
            },
        )

    return _common.ok_output()
