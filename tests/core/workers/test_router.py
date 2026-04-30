"""Router worker — rule-based routing + sync/async paths (T1.15)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.events import EventBus, EventEnvelope, EventType
from itsme.core.workers.router import KIND_TO_ROOM, Router, RouterDecision

RouterEnv = tuple[EventBus, Router, InMemoryMemPalaceAdapter]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[RouterEnv]:
    bus = EventBus(db_path=tmp_path / "events.db", capacity=200)
    adapter = InMemoryMemPalaceAdapter()
    router = Router(bus=bus, adapter=adapter, wing="wing_proj")
    try:
        yield bus, router, adapter
    finally:
        adapter.close()
        bus.close()


def _raw(
    bus: EventBus,
    content: str,
    kind: str | None = None,
    source: str = "explicit",
) -> EventEnvelope:
    return bus.emit(
        type=EventType.RAW_CAPTURED,
        source=source,
        payload={"content": content, "kind": kind},
    )


# ----------------------------------------------------------------- rule mapping
@pytest.mark.parametrize("kind", list(KIND_TO_ROOM))
def test_route_kind_explicit_for_each_known_kind(env: RouterEnv, kind: str) -> None:
    """A producer-supplied kind takes precedence over keyword inference."""
    bus, router, _ = env
    decision = router.route(_raw(bus, "anything goes here", kind=kind))
    assert decision.rule == "kind-explicit"
    assert decision.kind_used == kind
    assert decision.room == f"room_{KIND_TO_ROOM[kind]}"
    assert decision.wing == "wing_proj"


def test_route_unknown_kind_falls_through_to_keywords_or_general(env: RouterEnv) -> None:
    """Unknown kind is not a hard error; routing falls back."""
    bus, router, _ = env
    decision = router.route(_raw(bus, "free-form note", kind="bogus"))
    # 'bogus' is not in KIND_TO_ROOM; "free-form" matches no keyword;
    # so we end up in fallback.
    assert decision.rule == "fallback"
    assert decision.room == "room_general"


# ---------------------------------------------------------- keyword inference
@pytest.mark.parametrize(
    "content,expected_kind,rule_starts_with",
    [
        ("we decided to ship Friday", "decision", "keyword:decided"),
        ("I chose option B", "decision", "keyword:chose"),
        ("todo: refactor router", "todo", "keyword:todo"),
        ("I need to fix the bug", "todo", "keyword:need to"),
        ("I feel frustrated about it", "feeling", "keyword:i feel"),
        ("frustrated by the build", "feeling", "keyword:frustrated"),
        ("today the deploy went smooth", "event", "keyword:today"),
        ("at 3 we caught a regression", "event", "keyword:at 3"),
    ],
)
def test_route_keyword_inference(
    env: RouterEnv,
    content: str,
    expected_kind: str,
    rule_starts_with: str,
) -> None:
    """Keyword rules infer kind when producer didn't supply one."""
    bus, router, _ = env
    decision = router.route(_raw(bus, content))
    assert decision.kind_used == expected_kind
    assert decision.room == f"room_{KIND_TO_ROOM[expected_kind]}"
    assert decision.rule.startswith("keyword:")


def test_route_no_keyword_match_falls_back_to_general(env: RouterEnv) -> None:
    """No kind, no keyword → general."""
    bus, router, _ = env
    decision = router.route(_raw(bus, "the sky is the colour of television"))
    assert decision.rule == "fallback"
    assert decision.kind_used is None
    assert decision.room == "room_general"


def test_route_rejects_non_raw_envelope(env: RouterEnv) -> None:
    """Defensive: only raw.captured is routable."""
    bus, router, _ = env
    other = bus.emit(type=EventType.MEMORY_QUERIED, source="x", payload={})
    with pytest.raises(ValueError, match="raw.captured"):
        router.route(other)


def test_route_kind_explicit_wins_over_keywords(env: RouterEnv) -> None:
    """Producer-supplied kind beats keyword inference."""
    bus, router, _ = env
    # Content has 'decided' but kind is 'fact' — kind wins.
    decision = router.route(_raw(bus, "we decided X", kind="fact"))
    assert decision.rule == "kind-explicit"
    assert decision.kind_used == "fact"
    assert decision.room == "room_facts"


# ----------------------------------------------------------- route_and_store
def test_route_and_store_emits_routed_then_stored(env: RouterEnv) -> None:
    """memory.routed is emitted before the adapter write; memory.stored after."""
    bus, router, _adapter = env
    raw = _raw(bus, "key decision: rewrite", kind="decision")
    write_res = router.route_and_store(raw)

    routed = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
    stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])

    assert routed and stored
    assert routed[0].payload["raw_event_id"] == raw.id
    assert routed[0].payload["wing"] == "wing_proj"
    assert routed[0].payload["room"] == "room_decisions"
    assert routed[0].payload["kind_used"] == "decision"
    assert routed[0].payload["rule"] == "kind-explicit"

    assert stored[0].payload["drawer_id"] == write_res.drawer_id
    assert stored[0].payload["raw_event_id"] == raw.id


def test_route_and_store_writes_to_adapter(env: RouterEnv) -> None:
    """The drawer is searchable post-write."""
    bus, router, adapter = env
    raw = _raw(bus, "albatross facts", kind="fact")
    router.route_and_store(raw)
    hits = adapter.search("albatross")
    assert hits and "albatross" in hits[0].content


def test_route_and_store_rejects_empty_content(env: RouterEnv) -> None:
    """Empty content payload → ValueError, no adapter write."""
    bus, router, adapter = env
    raw = bus.emit(
        type=EventType.RAW_CAPTURED,
        source="explicit",
        payload={"content": "  ", "kind": "fact"},
    )
    with pytest.raises(ValueError, match="content"):
        router.route_and_store(raw)
    assert adapter.search("anything") == []


# ------------------------------------------------------------- consume_loop
def test_consume_loop_processes_unrouted_events(env: RouterEnv) -> None:
    """The async loop picks up raw.captured events from non-ignored sources."""
    bus, router, _adapter = env
    # Hook-style emit, not a sync remember
    bus.emit(
        type=EventType.RAW_CAPTURED,
        source="hook:cc:before-clear",
        payload={"content": "we decided to ship", "kind": None},
    )

    async def runner() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(router.consume_loop(stop=stop, poll_interval=0.05))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(runner())

    routed = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
    stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    assert routed and stored
    assert routed[0].payload["kind_used"] == "decision"


def test_consume_loop_skips_ignored_sources(env: RouterEnv) -> None:
    """``explicit`` source is already routed sync — loop must skip."""
    bus, router, _ = env
    # Don't actually call remember (no router); just emit an explicit
    # raw.captured with no follow-up so the loop has the chance to
    # mistakenly re-route it.
    bus.emit(
        type=EventType.RAW_CAPTURED,
        source="explicit",
        payload={"content": "decided X", "kind": "decision"},
    )

    async def runner() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            router.consume_loop(
                ignore_sources=("explicit",),
                stop=stop,
                poll_interval=0.05,
            )
        )
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(runner())

    routed = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
    assert routed == []


def test_consume_loop_dedups_via_memory_stored(env: RouterEnv) -> None:
    """A raw_event_id with a matching memory.stored is not re-routed.

    Dedup keys on ``memory.stored`` (post-write) so a failed write
    doesn't accidentally mark an envelope as "done".
    """
    bus, router, _ = env
    raw = _raw(bus, "decided to deploy", source="hook:before-exit")
    # Pre-route synchronously to seed both memory.routed AND memory.stored.
    router.route_and_store(raw)
    pre_stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    pre_routed = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
    assert len(pre_stored) == 1
    assert len(pre_routed) == 1

    async def runner() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(router.consume_loop(stop=stop, poll_interval=0.05))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(runner())

    # Loop must not have produced a second memory.routed/memory.stored.
    post_stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    post_routed = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
    assert len(post_stored) == 1
    assert len(post_routed) == 1
    assert post_stored[0].payload["raw_event_id"] == raw.id


def test_consume_loop_retries_after_write_failure(env: RouterEnv) -> None:
    """Critical: write failure must NOT poison the envelope.

    Regression for CodeRabbit PR#6 finding — earlier code keyed dedup
    on ``memory.routed`` (emitted before the adapter write). If
    ``adapter.write`` raised, the envelope had a routed event but no
    stored event, yet was incorrectly marked "done" and silently
    dropped. With dedup keyed on ``memory.stored``, a fresh consume
    loop on the same bus must successfully retry.
    """
    bus, router, adapter = env

    # Seed a hook-style raw.captured.
    raw = _raw(bus, "today we shipped", source="hook:before-exit")

    # Simulate a transient write failure by swapping the adapter's
    # ``write`` for one that raises. ``route_and_store`` will emit
    # memory.routed first, then blow up before memory.stored.
    original_write = adapter.write
    adapter.write = lambda **_: (_ for _ in ()).throw(RuntimeError("disk full"))  # type: ignore[method-assign]

    async def first_pass() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(router.consume_loop(stop=stop, poll_interval=0.05))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(first_pass())

    routed_after_fail = bus.tail(n=10, types=[EventType.MEMORY_ROUTED])
    stored_after_fail = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    # routed got emitted, stored did NOT (write failed).
    assert any(e.payload["raw_event_id"] == raw.id for e in routed_after_fail)
    assert not any(e.payload.get("raw_event_id") == raw.id for e in stored_after_fail)

    # Restore the working adapter and run a fresh consume loop —
    # simulates a process restart. The envelope must NOT be deduped
    # (no memory.stored exists for it) and the retry must succeed.
    adapter.write = original_write  # type: ignore[method-assign]

    async def second_pass() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(router.consume_loop(stop=stop, poll_interval=0.05))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(second_pass())

    stored_final = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    assert any(
        e.payload["raw_event_id"] == raw.id for e in stored_final
    ), "retry must persist the envelope after the write path recovers"
    # The drawer is searchable now.
    assert adapter.search("today")


def test_initial_cursor_returns_none(env: RouterEnv) -> None:
    """v0.0.1 simplification: always replay the ring window on boot."""
    bus, router, _ = env
    # Even with prior memory.routed events, cursor stays None.
    raw = _raw(bus, "decided X", source="hook:x")
    router.route_and_store(raw)
    assert router._initial_cursor() is None  # noqa: SLF001


# -------------------------------------------------------- RouterDecision
def test_router_decision_is_frozen() -> None:
    """RouterDecision is immutable so it can be embedded in event payloads."""
    d = RouterDecision(wing="w", room="r", kind_used="fact", rule="kind-explicit")
    with pytest.raises((AttributeError, Exception)):
        d.wing = "other"  # type: ignore[misc]
