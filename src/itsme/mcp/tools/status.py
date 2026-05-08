"""``status(scope?, format?)`` — observability feed (T1.12 + T1.22).

Surfaces recent events from the bus ring. Two formats:

* ``format='json'`` — :class:`itsme.core.StatusResult` as a dict.
  Stable wire shape; consumers (dashboards, tests) get every event
  field verbatim.
* ``format='feed'`` — a small dict with ``scope`` / ``count`` /
  ``summary`` / ``feed``. ``feed`` is a multi-line string designed
  to be readable when the IDE (CC / Codex) renders the tool result
  inline in the transcript. T1.22 makes this format actually
  human-friendly: per-event-type rendering with content snippets,
  drawer prefixes, dedup callouts, and a one-line summary header.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal, cast

from itsme.core import Memory
from itsme.core.api import StatusEvent

#: Hard upper bound on a single ``status`` request — keeps the feed
#: rendering and the JSON payload bounded for MCP transport.
MAX_LIMIT = 500

#: Max characters of ``content`` we splice into a feed line. Keeps a
#: 20-event feed comfortably under ~2KB even when every line carries
#: a snippet, and matches the "one-line in the transcript" goal.
_FEED_CONTENT_SNIPPET = 80

#: How many trailing chars of ``drawer_id`` / event ids we surface in
#: the feed. ULIDs share their leading 10 chars within the same second
#: (timestamp prefix) so a head-prefix would render look-alike ids in a
#: bursty session; the trailing 8 chars are entropy and distinguish by
#: eye in a 20-event window without dominating the line.
_FEED_ID_SUFFIX = 8


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
        format: ``json`` (machine-readable) or ``feed`` (human-readable
            text designed for the IDE transcript).
        limit: Max events (1 ≤ limit ≤ :data:`MAX_LIMIT`).

    Returns:
        For ``format='json'``: :class:`itsme.core.StatusResult` as a
        dict. For ``format='feed'``: a dict with ``scope`` / ``count`` /
        ``summary`` / ``feed`` keys. Both shapes are JSON-serialisable
        for MCP transport.
    """
    if not isinstance(scope, str) or scope not in {"recent", "today", "session"}:
        raise ValueError(f"scope must be 'recent' / 'today' / 'session'; got {scope!r}")
    if not isinstance(format, str) or format not in {"json", "feed"}:
        raise ValueError(f"format must be 'json' or 'feed'; got {format!r}")
    # bool is a subclass of int — reject it so ``limit=True`` isn't accepted.
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if limit > MAX_LIMIT:
        raise ValueError(f"limit must be a positive integer and <= {MAX_LIMIT}; got {limit}")

    result = memory.status(
        scope=cast(Literal["recent", "today", "session"], scope),
        limit=limit,
    )

    if format == "feed":
        return {
            "scope": result.scope,
            "count": result.count,
            "summary": _feed_summary_line(result.events),
            "feed": _render_feed(result.events),
        }
    return result.model_dump(mode="json")


# ---------------------------------------------------------------- rendering


def _render_feed(events: list[StatusEvent]) -> str:
    """Render a list of :class:`StatusEvent` as a human-readable feed.

    Newest-first order is preserved from :meth:`Memory.status`. One
    line per event; format::

        HH:MM:SS  TAG  one-line summary

    where ``TAG`` is a short, fixed-width-ish indicator picked per
    event type so the operator can scan the column visually.
    """
    if not events:
        return "(no events in window)"
    return "\n".join(_render_event(e) for e in events)


def _render_event(e: StatusEvent) -> str:
    """One feed line for a single event. Pure function — easy to test."""
    when = e.ts.strftime("%H:%M:%S")
    tag, summary = _render_payload(e.type, e.source, e.payload)
    return f"{when}  {tag}  {summary}"


def _render_payload(  # noqa: C901 — type-dispatch tree is simpler flat
    event_type: str,
    source: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    """Return ``(tag, summary)`` for one event.

    Kept as a single dispatch on ``event_type`` rather than a registry
    so the v0.0.1 surface stays grep-able — there are five types and
    one default. Adding a new EventType is a one-line edit here.
    """
    if event_type == "raw.captured":
        producer = _short_producer(source, payload)
        snippet = _content_snippet(payload.get("content"))
        return ("📥 raw  ", f"<{producer}> {snippet}")

    if event_type == "memory.routed":
        wing = payload.get("wing", "?")
        room = payload.get("room", "?")
        rule = payload.get("rule", "?")
        return ("🔀 route", f"→ {wing}/{room}  ({rule})")

    if event_type == "memory.stored":
        drawer = _short_id(payload.get("drawer_id"))
        room = payload.get("room", "?")
        return ("💾 store", f"✓ {room} drawer:{drawer}")

    if event_type == "memory.curated":
        reason = payload.get("reason", "?")
        if reason == "dedup":
            drawer = _short_id(payload.get("drawer_id"))
            producer = payload.get("producer_kind") or "?"
            return ("♻ dedup ", f"= <{producer}> → drawer:{drawer}")
        if reason == "crosslink":
            n = payload.get("links_inserted", 0)
            p = payload.get("pages_modified", 0)
            return ("🔗 xlink", f"+{n} links across {p} pages")
        if reason == "refresh":
            para = payload.get("paragraphs_removed", 0)
            hist = payload.get("history_dupes_removed", 0)
            return ("🧹 clean", f"-{para} para, -{hist} hist dupes")
        if reason == "merge_candidate":
            count = payload.get("count", 0)
            return ("⚠ merge", f"{count} duplicate page pair(s) detected")
        if reason == "invalidation":
            subj = payload.get("subject", "?")
            pred = payload.get("predicate", "?")
            obj = payload.get("object", "?")
            applied = payload.get("applied", False)
            mark = "✓" if applied else "○"
            return ("⏳ inval", f"{mark} {subj}.{pred}→{obj}")
        return ("⚙ curat", f"reason={reason}")

    if event_type == "memory.queried":
        question = _content_snippet(payload.get("question"))
        n = payload.get("hit_count", "?")
        mode = payload.get("mode", "?")
        return ("🔍 query", f"? '{question}' → {n} hits ({mode})")

    if event_type == "wiki.promoted":
        created = payload.get("pages_created", 0)
        updated = payload.get("pages_updated", 0)
        return ("📝 wiki ", f"+{created} new, ~{updated} updated")

    # Unknown / future event type — keep it observable with the source
    # tag rather than dropping silently.
    return (event_type[:7].ljust(7), f"<{source}>")


def _feed_summary_line(events: list[StatusEvent]) -> str:
    """One-line counts header.

    "12 events · 4 raw · 3 stored · 1 dedup · 1 query · 0 other"
    Skips zero-count buckets *except* the totals — operators care
    most about which buckets fired.

    ``memory.curated`` is bucketed by ``payload["reason"]`` rather
    than the bare event type so future curated reasons (rewrite,
    demote, …) don't get silently rolled up under "dedup".
    v0.0.1 only emits ``reason="dedup"``, but the line renderer
    already distinguishes — the header should too.
    """
    if not events:
        return "0 events"
    counts: Counter[str] = Counter(e.type for e in events)
    dedup_count = sum(
        1 for e in events if e.type == "memory.curated" and e.payload.get("reason") == "dedup"
    )
    crosslink_count = sum(
        1 for e in events if e.type == "memory.curated" and e.payload.get("reason") == "crosslink"
    )
    refresh_count = sum(
        1 for e in events if e.type == "memory.curated" and e.payload.get("reason") == "refresh"
    )
    merge_count = sum(
        1
        for e in events
        if e.type == "memory.curated" and e.payload.get("reason") == "merge_candidate"
    )
    inval_count = sum(
        1
        for e in events
        if e.type == "memory.curated" and e.payload.get("reason") == "invalidation"
    )
    total = sum(counts.values())
    bits = [f"{total} events"]
    # Order = visual scan priority: producer activity first, then
    # writes, then wiki, then curated subtypes, then queries.
    for label, n in (
        ("raw", counts.get("raw.captured", 0)),
        ("stored", counts.get("memory.stored", 0)),
        ("wiki", counts.get("wiki.promoted", 0)),
        ("dedup", dedup_count),
        ("xlink", crosslink_count),
        ("clean", refresh_count),
        ("merge", merge_count),
        ("inval", inval_count),
        ("query", counts.get("memory.queried", 0)),
        ("routed", counts.get("memory.routed", 0)),
    ):
        if n:
            bits.append(f"{n} {label}")
    return " · ".join(bits)


# ---------------------------------------------------------------- helpers


def _short_id(value: Any) -> str:
    """Return a short, stable suffix of an id-like value for the feed.

    Suffix not prefix: bus ULIDs share their leading 10 chars within
    the same second (timestamp portion), so a head-prefix renders
    visually identical ids in a bursty session.
    """
    if not isinstance(value, str) or not value:
        return "????????"
    return value[-_FEED_ID_SUFFIX:]


def _short_producer(source: str, payload: dict[str, Any]) -> str:
    """Pick the most informative short label for who emitted a raw.captured.

    Prefer ``producer_kind`` when the producer stamped one (post-T1.19);
    fall back to the raw ``source`` so older / external producers stay
    legible.
    """
    pk = payload.get("producer_kind")
    if isinstance(pk, str) and pk:
        return pk
    return source or "?"


def _content_snippet(content: Any) -> str:
    """Trim *content* to a single line of ≤ :data:`_FEED_CONTENT_SNIPPET` chars.

    Newlines / tabs are replaced with single spaces so the feed stays
    one-line-per-event; truncation is marked with an ellipsis so the
    reader knows the original was longer.
    """
    if not isinstance(content, str) or not content:
        return "(empty)"
    flat = " ".join(content.split())
    if len(flat) <= _FEED_CONTENT_SNIPPET:
        return flat
    return flat[: _FEED_CONTENT_SNIPPET - 1].rstrip() + "…"
