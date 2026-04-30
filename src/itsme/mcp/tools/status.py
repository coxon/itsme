"""``status(scope?, format?)`` — observability feed (T1.12).

Surfaces recent events. v0.0.1 returns JSON or a small newline-
separated feed string; richer formats (Markdown, OTLP) come later.
"""

from __future__ import annotations

from typing import Any

from itsme.core import Memory


def status_handler(
    memory: Memory,
    *,
    scope: str = "recent",
    format: str = "json",  # noqa: A002 — matches MCP arg name
    limit: int = 20,
) -> dict[str, Any]:
    """Validate inputs and dispatch to :meth:`Memory.status`.

    Args:
        memory: Process-wide :class:`Memory` instance.
        scope: ``recent`` / ``today`` / ``session``.
        format: ``json`` (machine-readable) or ``feed`` (newline-
            joined human-readable strings).
        limit: Max events.

    Returns:
        For ``format='json'``: :class:`itsme.core.StatusResult` as a
        dict.  For ``format='feed'``: a small dict with a single
        ``feed`` key containing a multi-line string. Either way the
        return type is JSON-serialisable for MCP transport.
    """
    if scope not in {"recent", "today", "session"}:
        raise ValueError(f"scope must be 'recent' / 'today' / 'session'; got {scope!r}")
    if format not in {"json", "feed"}:
        raise ValueError(f"format must be 'json' or 'feed'; got {format!r}")
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    result = memory.status(scope=scope, limit=limit)  # type: ignore[arg-type]

    if format == "feed":
        lines = [
            f"{e.ts.isoformat()} [{e.type}] {e.source}: {_feed_summary(e.payload)}"
            for e in result.events
        ]
        return {
            "scope": result.scope,
            "count": result.count,
            "feed": "\n".join(lines),
        }
    return result.model_dump(mode="json")


def _feed_summary(payload: dict[str, Any]) -> str:
    """Compact one-line summary of an event payload for the feed."""
    if not payload:
        return "(no payload)"
    keys = ", ".join(sorted(payload.keys()))
    return f"keys={keys}"
