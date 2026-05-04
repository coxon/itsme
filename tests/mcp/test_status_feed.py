"""T1.22 — IDE-friendly ``status(format='feed')`` rendering.

The feed format is what an operator actually reads when CC dumps a
``status`` tool result inline in the transcript. These tests pin the
shape of each line so a future refactor doesn't silently regress the
human-readability contract:

* one line per event, newest-first
* fixed-ish-width tag column for visual scanning
* content snippet (≤ 80 chars, single line) for ``raw.captured``
* drawer prefix (8 chars) for ``memory.stored`` / ``memory.curated``
* dedup events surface ``producer_kind`` so cross-producer collisions
  are obvious at a glance
* summary header counts events by type
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from itsme.core import Memory
from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.api import StatusEvent
from itsme.core.events import EventBus
from itsme.mcp.tools.status import (
    _feed_summary_line,
    _render_event,
    _render_feed,
    _render_payload,
    status_handler,
)


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[Memory]:
    bus = EventBus(db_path=tmp_path / "events.db", capacity=100)
    m = Memory(bus=bus, adapter=InMemoryMemPalaceAdapter(), project="testproj")
    yield m
    m.close()


def _evt(
    event_type: str,
    *,
    source: str = "explicit",
    payload: dict[str, Any] | None = None,
    when: datetime | None = None,
) -> StatusEvent:
    return StatusEvent(
        id="01KQR" + ("0" * 21),
        ts=when or datetime(2026, 5, 4, 12, 34, 56, tzinfo=UTC),
        type=event_type,
        source=source,
        payload=payload or {},
    )


# ============================================================
# _render_payload — per-event-type rendering
# ============================================================


def test_render_raw_captured_uses_producer_kind_when_stamped() -> None:
    """T1.19 stamps producer_kind; T1.22 renders it as the producer label."""
    tag, summary = _render_payload(
        "raw.captured",
        "hook:before-exit",
        {"content": "decided to ship", "producer_kind": "hook:lifecycle"},
    )
    assert "raw" in tag
    assert "hook:lifecycle" in summary
    assert "decided to ship" in summary


def test_render_raw_captured_falls_back_to_source_without_producer_kind() -> None:
    """Pre-T1.19 / external producers still get a legible line."""
    _, summary = _render_payload(
        "raw.captured",
        "explicit",
        {"content": "hello"},
    )
    # source-prefix in angle-brackets, no producer_kind available.
    assert "<explicit>" in summary
    assert "hello" in summary


def test_render_raw_captured_truncates_long_content() -> None:
    """Content ≥ 80 chars is truncated with an ellipsis to keep one line."""
    long = "x" * 200
    _, summary = _render_payload("raw.captured", "explicit", {"content": long})
    # "…" sentinel must be present, full string must not.
    assert "…" in summary
    assert "x" * 200 not in summary
    # The whole line stays bounded around the snippet limit.
    assert len(summary) < 200


def test_render_raw_captured_flattens_newlines_to_single_line() -> None:
    """Multi-line transcripts must not break the one-line-per-event invariant."""
    _, summary = _render_payload(
        "raw.captured",
        "hook:before-exit",
        {"content": "line one\nline two\n\tline three", "producer_kind": "hook:lifecycle"},
    )
    assert "\n" not in summary
    assert "\t" not in summary
    assert "line one" in summary and "line two" in summary


def test_render_raw_captured_handles_empty_content() -> None:
    """Defensive: missing / non-string content surfaces ``(empty)``."""
    _, summary = _render_payload("raw.captured", "explicit", {})
    assert "(empty)" in summary


def test_render_memory_routed_shows_wing_room_rule() -> None:
    """``routed`` line lets the operator see why the kind/keyword fired."""
    tag, summary = _render_payload(
        "memory.routed",
        "worker:router",
        {"wing": "wing_proj", "room": "room_facts", "rule": "kind-explicit"},
    )
    assert "routed" in tag
    assert "wing_proj/room_facts" in summary
    assert "kind-explicit" in summary


def test_render_memory_stored_shows_room_and_drawer_suffix() -> None:
    """``stored`` line carries an 8-char drawer suffix.

    Suffix not prefix because the ring's ULIDs share their leading
    timestamp chars within the same second — a head-prefix would
    render visually identical ids in a bursty session.
    """
    drawer = "01KQR" + "ABCDEFGHIJKLMNOPQRSTU"  # 26-char ULID-ish
    tag, summary = _render_payload(
        "memory.stored",
        "adapter:mempalace",
        {"drawer_id": drawer, "room": "room_decisions"},
    )
    assert "stored" in tag
    assert "room_decisions" in summary
    assert "drawer:NOPQRSTU" in summary  # last 8 chars surfaced (entropy)
    assert drawer not in summary  # full id NOT surfaced


def test_render_memory_curated_dedup_shows_producer_kind() -> None:
    """Dedup events surface the deduped producer for cross-producer triage.

    The whole point of T1.19's `memory.curated(reason="dedup")` is
    "two producers collided" — the feed has to make that visible.
    """
    tag, summary = _render_payload(
        "memory.curated",
        "worker:router",
        {
            "reason": "dedup",
            "producer_kind": "hook:lifecycle",
            "drawer_id": "01KQRABCDEFGHIJKLMNOPQRSTU",
            "content_hash": "deadbeef",
            "original_stored_event_id": "01KQOLD",
        },
    )
    assert "dedup" in tag
    assert "hook:lifecycle" in summary
    assert "drawer:NOPQRSTU" in summary  # 8-char suffix


def test_render_memory_curated_unknown_reason_still_observable() -> None:
    """Future curated reasons (rewrite, demote …) shouldn't render as blank."""
    tag, summary = _render_payload(
        "memory.curated",
        "worker:future",
        {"reason": "rewrite"},
    )
    assert "curated" in tag
    assert "rewrite" in summary


def test_render_memory_queried_shows_question_and_hit_count() -> None:
    """``query`` line: see what was asked + how many hits — enough for triage."""
    tag, summary = _render_payload(
        "memory.queried",
        "reader",
        {"question": "what did we decide about deploys?", "hit_count": 3},
    )
    assert "query" in tag
    assert "what did we decide" in summary
    assert "3 hits" in summary


def test_render_unknown_event_type_keeps_source_visible() -> None:
    """Forward-compat: a future EventType still produces a non-empty line."""
    tag, summary = _render_payload("future.event", "future:source", {})
    assert tag.strip() != ""
    assert "<future:source>" in summary


# ============================================================
# _render_event — line-level format
# ============================================================


def test_render_event_starts_with_HHMMSS_clock() -> None:
    """Each line opens with a wall-clock so operators can correlate by eye.

    Full ISO timestamps make the column too noisy in CC's transcript;
    HH:MM:SS is enough since the feed is a *recent* window.
    """
    e = _evt(
        "raw.captured",
        source="explicit",
        payload={"content": "x", "producer_kind": "explicit"},
        when=datetime(2026, 5, 4, 7, 8, 9, tzinfo=UTC),
    )
    line = _render_event(e)
    assert line.startswith("07:08:09  ")


def test_render_event_is_a_single_line() -> None:
    """The feed contract is one event per line; even multiline payloads collapse."""
    e = _evt(
        "raw.captured",
        source="hook:before-exit",
        payload={"content": "a\nb\nc", "producer_kind": "hook:lifecycle"},
    )
    assert "\n" not in _render_event(e)


# ============================================================
# _render_feed — multi-line aggregation
# ============================================================


def test_render_feed_empty_window_has_explicit_marker() -> None:
    """An empty ring should render an obvious "(no events)" line, not blank.

    A blank string in the IDE looks like a tool failure; the explicit
    marker makes "nothing happened in this window" unambiguous.
    """
    assert _render_feed([]) == "(no events in window)"


def test_render_feed_preserves_input_order() -> None:
    """:meth:`Memory.status` returns newest-first; the renderer must not reorder."""
    a = _evt(
        "raw.captured",
        when=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        payload={"content": "alpha", "producer_kind": "explicit"},
    )
    b = _evt(
        "raw.captured",
        when=datetime(2026, 5, 4, 12, 0, 1, tzinfo=UTC),
        payload={"content": "beta", "producer_kind": "explicit"},
    )
    feed = _render_feed([b, a])  # newest first
    lines = feed.split("\n")
    assert "beta" in lines[0]
    assert "alpha" in lines[1]


# ============================================================
# _feed_summary_line — header counts
# ============================================================


def test_summary_line_counts_per_type_and_skips_zero_buckets() -> None:
    """Only fired buckets show; the total is always present.

    Zero-count buckets in the header just add noise — operators care
    that "1 dedup fired" not "0 routed, 0 dedup, 0 query".
    """
    events = [
        _evt("raw.captured"),
        _evt("raw.captured"),
        _evt("memory.stored"),
        _evt("memory.curated", payload={"reason": "dedup"}),
    ]
    line = _feed_summary_line(events)
    assert line.startswith("4 events")
    assert "2 raw" in line and "1 stored" in line and "1 dedup" in line
    # zero-count buckets aren't rendered
    assert "query" not in line
    assert "routed" not in line


def test_summary_line_handles_empty_window() -> None:
    """No events → ``"0 events"`` (matches the feed's empty marker)."""
    assert _feed_summary_line([]) == "0 events"


def test_summary_line_buckets_curated_by_reason_not_event_type() -> None:
    """A future ``reason="rewrite"`` curated must not roll up under "dedup".

    CR PR#13 r1: the line renderer already distinguishes by
    ``payload["reason"]``; the summary header has to do the same so
    forward-compat curated reasons (rewrite, demote …) don't get
    silently mis-attributed when v0.0.2/0.0.3 starts emitting them.
    """
    events = [
        _evt("memory.curated", payload={"reason": "dedup"}),
        _evt("memory.curated", payload={"reason": "dedup"}),
        _evt("memory.curated", payload={"reason": "rewrite"}),
    ]
    line = _feed_summary_line(events)
    assert line.startswith("3 events")
    assert "2 dedup" in line
    assert "1 curated" in line
    # The "dedup" label must NOT also count the rewrite one.
    assert "3 dedup" not in line


def test_summary_line_no_curated_bucket_when_only_dedup() -> None:
    """Pure-dedup windows still skip the (zero) ``curated`` bucket.

    Pinned: today's v0.0.1 only emits ``reason="dedup"`` — the new
    ``curated`` bucket must stay invisible until a non-dedup curated
    reason actually fires.
    """
    events = [_evt("memory.curated", payload={"reason": "dedup"})]
    line = _feed_summary_line(events)
    assert "1 dedup" in line
    assert "curated" not in line


# ============================================================
# Integration via status_handler — end-to-end through Memory
# ============================================================


def test_status_handler_feed_renders_remember_then_ask(memory: Memory) -> None:
    """One remember + one ask produces a feed with all expected event types."""
    memory.remember("decided to roll back", kind="decision")
    memory.ask("decided")

    out = status_handler(memory, scope="recent", format="feed", limit=20)
    assert isinstance(out["feed"], str)
    assert "raw" in out["feed"]
    assert "stored" in out["feed"]
    assert "query" in out["feed"]
    assert "decided to roll back" in out["feed"]
    assert "decided" in out["feed"]  # the question

    # Summary covers all fired buckets.
    assert "raw" in out["summary"]
    assert "stored" in out["summary"]
    assert "query" in out["summary"]


def test_status_handler_feed_surfaces_dedup(memory: Memory) -> None:
    """The dedup short-circuit must show up as a ``dedup`` line in the feed.

    Without this T1.19 dedup is invisible to the operator — and the
    whole reason we surface ``memory.curated`` is so they can see it.
    """
    memory.remember("idempotent fact", kind="fact")
    memory.remember("idempotent fact", kind="fact")  # dedup

    out = status_handler(memory, scope="recent", format="feed", limit=20)
    assert "dedup" in out["feed"]
    assert "explicit" in out["feed"]  # producer_kind surfaced
    assert "1 dedup" in out["summary"]


def test_status_handler_json_format_unchanged(memory: Memory) -> None:
    """JSON shape is the wire contract — must NOT change with T1.22.

    Pinned regression: T1.22 only enriches the ``feed`` rendering;
    machine consumers that pass ``format='json'`` get exactly the
    :class:`StatusResult` they did before.
    """
    memory.remember("payload pin", kind="fact")
    out = status_handler(memory, scope="recent", format="json", limit=10)
    assert set(out.keys()) == {"scope", "count", "events"}
    # No summary / feed leak into JSON.
    assert "summary" not in out
    assert "feed" not in out


def test_status_handler_feed_empty_window(memory: Memory) -> None:
    """A fresh Memory yields the explicit empty marker in the feed."""
    out = status_handler(memory, scope="recent", format="feed", limit=20)
    assert out["count"] == 0
    assert out["feed"] == "(no events in window)"
    assert out["summary"] == "0 events"
