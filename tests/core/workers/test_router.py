"""Router worker — rule-based routing + sync/async paths (T1.15)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.dedup import content_hash, producer_kind_from_source
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
    *,
    stamp_dedup: bool = False,
) -> EventEnvelope:
    payload: dict[str, object] = {"content": content, "kind": kind}
    if stamp_dedup:
        payload["content_hash"] = content_hash(content)
        payload["producer_kind"] = producer_kind_from_source(source)
    return bus.emit(
        type=EventType.RAW_CAPTURED,
        source=source,
        payload=payload,
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


# ============================================================
# T1.19 — content-hash cross-producer dedup
# ============================================================


def test_route_and_store_mirrors_content_hash_into_memory_stored(env: RouterEnv) -> None:
    """``memory.stored`` payload carries the same ``content_hash`` as raw.captured.

    The router scans ``memory.stored`` newest-first to look up prior
    drawers — mirroring the hash there keeps the dedup walk a single-
    pass over one event type.
    """
    bus, router, _ = env
    raw = _raw(bus, "decided to ship Friday", kind="decision", stamp_dedup=True)
    router.route_and_store(raw)

    stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    assert stored
    assert stored[0].payload["content_hash"] == content_hash("decided to ship Friday")


def test_route_and_store_dedups_same_content_within_producer(env: RouterEnv) -> None:
    """Two captures of identical content from the same producer dedup.

    Second call must NOT emit a fresh memory.stored. Instead it emits
    one memory.curated (reason=dedup) and returns the same drawer_id
    as the first call.
    """
    bus, router, _ = env
    first = _raw(bus, "decided X", kind="decision", stamp_dedup=True)
    res1 = router.route_and_store(first)

    second = _raw(bus, "decided X", kind="decision", stamp_dedup=True)
    res2 = router.route_and_store(second)

    # Same drawer surfaced.
    assert res2.drawer_id == res1.drawer_id
    assert res2.wing == res1.wing
    assert res2.room == res1.room

    # Exactly one memory.stored across both calls.
    stored = bus.tail(n=20, types=[EventType.MEMORY_STORED])
    assert len(stored) == 1

    # Exactly one memory.curated, with reason=dedup, pointing at the
    # original stored event id.
    curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
    assert len(curated) == 1
    cur = curated[0]
    assert cur.payload["reason"] == "dedup"
    assert cur.payload["raw_event_id"] == second.id
    assert cur.payload["original_stored_event_id"] == stored[0].id
    assert cur.payload["drawer_id"] == res1.drawer_id
    assert cur.payload["content_hash"] == content_hash("decided X")
    assert cur.payload["producer_kind"] == "explicit"


def test_route_and_store_dedups_cross_producer(env: RouterEnv) -> None:
    """Explicit remember + hook capture of same content → dedup.

    The cross-producer case is the whole point of T1.19: a real CC
    session has the user calling ``remember("decided X")`` mid-session
    and then SessionEnd later salvaging a transcript tail that contains
    the same line. Without dedup, MemPalace ends up with two drawers
    for the same fact.
    """
    bus, router, _ = env
    explicit = _raw(bus, "decided to roll back", kind="decision", stamp_dedup=True)
    router.route_and_store(explicit)

    hook = _raw(
        bus,
        "decided to roll back",
        source="hook:before-exit",
        stamp_dedup=True,
    )
    res2 = router.route_and_store(hook)

    stored = bus.tail(n=20, types=[EventType.MEMORY_STORED])
    curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
    assert len(stored) == 1
    assert len(curated) == 1
    # Curated payload records the *hook* as the deduped producer.
    assert curated[0].payload["producer_kind"] == "hook:lifecycle"
    assert curated[0].payload["raw_event_id"] == hook.id
    assert res2.drawer_id == stored[0].payload["drawer_id"]


def test_route_and_store_normalises_whitespace_for_dedup(env: RouterEnv) -> None:
    """``"X"`` and ``"X\\n"`` collide via the strip()-then-hash recipe.

    Transcript tails almost always carry a trailing newline; without
    normalisation an explicit remember + hook pair would always escape
    dedup.
    """
    bus, router, _ = env
    a = _raw(bus, "decided to deploy", kind="decision", stamp_dedup=True)
    router.route_and_store(a)

    b = _raw(bus, "decided to deploy\n", kind="decision", stamp_dedup=True)
    router.route_and_store(b)

    stored = bus.tail(n=20, types=[EventType.MEMORY_STORED])
    curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
    assert len(stored) == 1
    assert len(curated) == 1


def test_route_and_store_does_not_dedup_different_content(env: RouterEnv) -> None:
    """Two captures with distinct content produce two drawers.

    Sanity check that dedup is keyed on content_hash, not on type/wing.
    """
    bus, router, _ = env
    a = _raw(bus, "decided to ship Friday", kind="decision", stamp_dedup=True)
    b = _raw(bus, "decided to ship Monday", kind="decision", stamp_dedup=True)
    router.route_and_store(a)
    router.route_and_store(b)

    stored = bus.tail(n=20, types=[EventType.MEMORY_STORED])
    curated = bus.tail(n=20, types=[EventType.MEMORY_CURATED])
    assert len(stored) == 2
    assert curated == []


def test_route_and_store_without_content_hash_still_writes(env: RouterEnv) -> None:
    """Backward-compat: a raw.captured without ``content_hash`` skips dedup.

    All v0.0.1 producers stamp the hash, but the router must not
    require it (envelopes seeded by older code paths or external
    producers should still route normally — they just bypass dedup).
    """
    bus, router, _ = env
    # No stamp_dedup → no content_hash in payload.
    raw = _raw(bus, "decided Z", kind="decision")
    res = router.route_and_store(raw)

    stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    assert len(stored) == 1
    assert res.drawer_id == stored[0].payload["drawer_id"]
    # content_hash mirrored as None on the stored payload.
    assert stored[0].payload["content_hash"] is None


def test_dedup_does_not_count_failed_writes(env: RouterEnv) -> None:
    """Dedup window is keyed on memory.stored — failed writes don't poison.

    Pinned regression for the post-write keying invariant: if dedup were
    keyed on raw.captured (or memory.routed), a failed adapter write
    would still mark the content_hash as "seen" and the retry path
    would silently surface a non-existent prior drawer.
    """
    bus, router, adapter = env

    # First capture — adapter write blows up before memory.stored.
    a = _raw(bus, "ephemeral content", kind="decision", stamp_dedup=True)
    original_write = adapter.write
    adapter.write = lambda **_: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("disk full")
    )
    with pytest.raises(RuntimeError):
        router.route_and_store(a)

    # No memory.stored, no memory.curated emitted yet.
    assert bus.tail(n=10, types=[EventType.MEMORY_STORED]) == []
    assert bus.tail(n=10, types=[EventType.MEMORY_CURATED]) == []

    # Second capture of the *same* content with adapter restored — must
    # actually persist (not falsely treated as a dedup hit).
    adapter.write = original_write  # type: ignore[method-assign]
    b = _raw(bus, "ephemeral content", kind="decision", stamp_dedup=True)
    router.route_and_store(b)

    stored = bus.tail(n=10, types=[EventType.MEMORY_STORED])
    curated = bus.tail(n=10, types=[EventType.MEMORY_CURATED])
    assert len(stored) == 1
    assert curated == []
    # The drawer is searchable now.
    assert adapter.search("ephemeral")


def test_dedup_skip_does_not_emit_extra_routed_or_stored(env: RouterEnv) -> None:
    """Dedup short-circuit emits ONLY memory.curated, nothing else.

    Re-emitting memory.routed/memory.stored on the dedup path would
    break the consume-loop's raw_event_id-keyed dedup invariant ("one
    stored per raw") and double-count routing in observability tools.
    """
    bus, router, _ = env
    first = _raw(bus, "decided Q", kind="decision", stamp_dedup=True)
    router.route_and_store(first)

    routed_before = len(bus.tail(n=20, types=[EventType.MEMORY_ROUTED]))
    stored_before = len(bus.tail(n=20, types=[EventType.MEMORY_STORED]))

    # Second call — same content, must short-circuit.
    second = _raw(bus, "decided Q", kind="decision", stamp_dedup=True)
    router.route_and_store(second)

    routed_after = len(bus.tail(n=20, types=[EventType.MEMORY_ROUTED]))
    stored_after = len(bus.tail(n=20, types=[EventType.MEMORY_STORED]))

    # No new routed / no new stored.
    assert routed_after == routed_before
    assert stored_after == stored_before
    # Exactly one new curated.
    assert len(bus.tail(n=20, types=[EventType.MEMORY_CURATED])) == 1
