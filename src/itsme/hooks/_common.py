"""Shared hook utilities: stdin parsing, transcript reading, bus wiring.

v0.0.1 invariants:

* **Never block the IDE.** Hook failures exit 0 with a stderr log; CC
  hooks that exit non-zero are surfaced as errors in the UI, which is
  the opposite of what a passive-capture plugin wants.
* **Respect ``$ITSME_HOOKS_DISABLED``** — users who want to silence
  itsme without uninstalling the plugin flip this and hooks become
  no-ops.
* **Bus path and project name match the MCP server.** Both read
  ``$ITSME_DB_PATH`` / ``$ITSME_PROJECT`` so a hook process writes into
  the same ring the MCP server's router is polling.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from itsme.core.events import EventBus
from itsme.core.filters.envelope import strip_envelopes

# --------------------------------------------------------------- config


def hooks_disabled() -> bool:
    """True if ``$ITSME_HOOKS_DISABLED`` is set to a truthy value.

    Accepts ``1``/``true``/``yes`` (case-insensitive) as on; empty,
    ``0``, ``false``, ``no`` are off. Anything else is treated as off to
    match "don't silently enable on weird values".
    """
    raw = os.environ.get("ITSME_HOOKS_DISABLED", "").strip().lower()
    return raw in {"1", "true", "yes"}


def resolve_db_path() -> Path:
    """Honor ``$ITSME_DB_PATH``, else ``~/.itsme/events.db``.

    Must stay in lockstep with ``itsme.mcp.server._resolve_db_path`` or
    hooks and MCP end up writing to different rings.
    """
    raw = os.environ.get("ITSME_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".itsme" / "events.db"


def resolve_project() -> str:
    """Project label used for the wing prefix. Mirrors MCP server."""
    return os.environ.get("ITSME_PROJECT", "default")


def resolve_state_dir() -> Path:
    """Per-session hook state (``~/.itsme/state/`` or ``$ITSME_STATE_DIR``).

    Currently only used by context-pressure's debounce state files; kept
    here so any future hook can share the same scheme. Tests override
    via ``$ITSME_STATE_DIR`` to avoid leaking state across runs.
    """
    raw = os.environ.get("ITSME_STATE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".itsme" / "state"


# --------------------------------------------------------------- stdin


def load_hook_input(stdin_text: str) -> dict[str, Any]:
    """Parse the CC hook JSON payload from stdin.

    Raises:
        ValueError: *stdin_text* is empty or not a JSON object.
    """
    if not stdin_text.strip():
        raise ValueError("hook input is empty (expected CC JSON on stdin)")
    try:
        data = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"hook input is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"hook input must be a JSON object, got {type(data).__name__}")
    return data


# -------------------------------------------------------- transcript IO


def _extract_message_text(entry: dict[str, Any]) -> str:
    """Pull plain text out of one CC transcript JSONL row.

    CC stores each turn as ``{"type": "user"|"assistant", "message":
    {"content": str | list[ContentBlock]}}``. We flatten text blocks
    and ignore tool-use / tool-result blocks for the snapshot.

    T2.0a: applies envelope stripping to remove CC control blocks
    (``<command-name>``, ``<command-args>``, etc.) that pollute drawers.
    """
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return strip_envelopes(content)
    if isinstance(content, list):
        pieces: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    pieces.append(text)
        raw = "\n".join(pieces)
        return strip_envelopes(raw)
    return ""


def _iter_transcript_texts(path: Path) -> list[str]:
    """Yield per-turn plain-text strings in chronological order.

    Missing/empty files produce an empty list. Malformed JSONL rows are
    silently skipped — CC occasionally writes partial lines at tail
    when a session ends abruptly, and we'd rather drop those than fail.

    v0.0.1 reads the whole file in one go. CC transcripts cap well under
    single-digit MB for realistic sessions and the bounded salvage
    already enforces the *output* size, so linear-in-filesize here is
    cheap — measured <20ms for 5MB on commodity disk. v0.0.2 will swap
    this for a backward block-reader (seek from EOF, chunk by 64KB,
    parse complete lines) once we have a tokenizer that justifies the
    complexity. Tracked: ROADMAP v0.0.2 §P1.
    """
    if not path.exists():
        return []
    texts: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        text = _extract_message_text(entry)
        if text:
            texts.append(text)
    return texts


def read_transcript_tail(path: Path, *, max_chars: int) -> str:
    """Concatenate the newest turns until *max_chars* is reached.

    Returns at most *max_chars* characters, chronological order. Used by
    lifecycle hooks (before-exit/before-compact) to snapshot a bounded
    salvage window rather than trying to dump the whole transcript.
    """
    if max_chars <= 0:
        return ""
    texts = _iter_transcript_texts(path)
    if not texts:
        return ""
    # Walk from newest back until we have enough, then reverse for
    # chronological output.
    collected: list[str] = []
    total = 0
    for text in reversed(texts):
        collected.append(text)
        total += len(text) + 1  # +1 for the join '\n'
        if total >= max_chars:
            break
    result = "\n".join(reversed(collected))
    return result[-max_chars:]


def read_transcript_full(path: Path) -> str:
    """Concatenate every turn. For size estimation, not for storage."""
    texts = _iter_transcript_texts(path)
    return "\n".join(texts)


def estimate_tokens(text: str) -> int:
    """Rough token count: chars/4 floored.

    v0.0.1 uses this for context-pressure gauging. A ±30% error is fine
    for debounce decisions; exact tokenization would require a
    model-specific tokenizer and is not worth the dependency weight.
    """
    return max(0, len(text) // 4)


# --------------------------------------------------------------- bus


def open_bus(*, capacity: int = 500) -> EventBus:
    """Open a fresh :class:`EventBus` pointed at the shared ring.

    Hook processes are short-lived; the caller is expected to
    ``.close()`` the bus before exit so the sqlite WAL checkpoint
    isn't left dangling.
    """
    return EventBus(db_path=resolve_db_path(), capacity=capacity)


# ----------------------------------------------------------- output


def ok_output(*, system_message: str | None = None) -> dict[str, Any]:
    """Standard CC hook success output.

    ``suppressOutput=True`` because these hooks run on every prompt /
    tool call and we don't want them to spam the transcript. The
    MCP ``status`` verb is the right surface for observability.
    """
    out: dict[str, Any] = {"continue": True, "suppressOutput": True}
    if system_message:
        out["systemMessage"] = system_message
    return out
