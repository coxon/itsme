"""Lifecycle hooks — T1.17.

Handles CC's ``SessionEnd`` and ``PreCompact`` events. Both do the same
thing: read a bounded tail of the session transcript and append it to
the events ring as ``raw.captured`` with a hook-specific source label.
The router's background consume loop picks the captures up and routes
them to MemPalace.

Why one function with a ``source`` knob rather than two sibling modules?
Because the shape of the work is identical — only the attribution
string differs. A single function keeps the invariant "lifecycle
triggers salvage a transcript slice" in one place.

For v0.0.1 we copy the slice verbatim into the event payload. v0.0.2
can switch to a lighter ``transcript_ref`` + small excerpt if storage
pressure becomes an issue (see ARCHITECTURE §6.2 on hybrid refs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    """Emit a transcript tail as ``raw.captured``.

    Args:
        stdin_text: Raw JSON the CC runtime piped to the hook process.
        bus: Open :class:`EventBus` (caller owns lifecycle).
        source: Event source label — ``"hook:before-exit"`` for
            SessionEnd, ``"hook:before-compact"`` for PreCompact.
        max_chars: Upper bound on captured text (default 10K).

    Returns:
        The CC hook output dict (``continue=True`` always — hooks must
        not block the IDE on capture failures).
    """
    # Short-circuit before parsing stdin: a disabled user with malformed
    # CC input would otherwise still see a ValueError surface in stderr.
    # The CLI already short-circuits earlier, but direct callers (tests,
    # future router) get the same guarantee here.
    if _common.hooks_disabled():
        return _common.ok_output()
    payload_in = _common.load_hook_input(stdin_text)

    transcript_path_raw = payload_in.get("transcript_path")
    content = ""
    if isinstance(transcript_path_raw, str) and transcript_path_raw:
        content = _common.read_transcript_tail(
            Path(transcript_path_raw),
            max_chars=max_chars,
        )
    if not content.strip():
        # No transcript text available (empty session, missing file,
        # or all tool-only turns). Skip the emit — a raw.captured with
        # empty content would just get dropped by the router's
        # validation anyway.
        return _common.ok_output()

    bus.emit(
        type=EventType.RAW_CAPTURED,
        source=source,
        payload={
            "content": content,
            "kind": None,
            # T1.19: cross-producer dedup keys. The router will skip
            # this capture if an explicit ``remember()`` (or an earlier
            # hook fire) already stored exactly the same content.
            "content_hash": content_hash(content),
            "producer_kind": producer_kind_from_source(source),
            "hook_event": payload_in.get("hook_event_name"),
            "session_id": payload_in.get("session_id"),
            "transcript_ref": {"path": transcript_path_raw},
            "cwd": payload_in.get("cwd"),
        },
    )
    return _common.ok_output()
