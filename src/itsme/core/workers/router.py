"""Router worker — decides wing/room and persists to MemPalace.

Two execution modes (ARCHITECTURE §6.1, §8.2):

* **Fast-path (sync)** — :meth:`Router.route_and_store` is called
  in-process by ``Memory.remember`` for explicit writes. The caller
  awaits the drawer id.
* **Loop (async)** — :meth:`Router.consume_loop` polls the events
  ring for ``raw.captured`` produced by *other* sources (hooks,
  background workers) and routes them in batches. Used by the
  asyncio scheduler in :mod:`itsme.core.workers.scheduler`.

Routing strategy (v0.0.1, no LLM):

1. If the envelope payload carries ``kind`` → rule mapping
   (``decision → room_decisions``, ``fact → room_facts``, …).
2. Otherwise → simple keyword inference over the content.
3. Else → ``room_general``.

Each routed envelope produces two events: ``memory.routed`` (the
decision log, observable / debuggable) and ``memory.stored`` (after
the adapter write succeeds).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from itsme.core.adapters import MemPalaceAdapter, MemPalaceWriteResult
from itsme.core.adapters.naming import room as _room
from itsme.core.events import EventBus, EventEnvelope, EventType

# ----------------------------------------------------------------------- rules

#: Direct mapping when the producer supplied ``kind``.
KIND_TO_ROOM: Final[dict[str, str]] = {
    "decision": "decisions",
    "fact": "facts",
    "feeling": "feelings",
    "todo": "todos",
    "event": "events",
}

#: Keyword → kind inference. Order matters — first match wins. Matches
#: are case-insensitive whole-word.  Patterns are conservative on
#: purpose; the goal is "obvious cases route correctly", not full NLU.
_KEYWORD_RULES: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r"\b(decided|decide|chose|picked)\b", re.I), "decision"),
    (re.compile(r"\b(todo|task|need to|must|should)\b", re.I), "todo"),
    (re.compile(r"\b(i feel|feeling|frustrated|happy|annoyed|tired)\b", re.I), "feeling"),
    (
        re.compile(
            r"\b(at \d|today|yesterday|tomorrow"
            r"|on monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.I,
        ),
        "event",
    ),
]


@dataclass(frozen=True)
class RouterDecision:
    """The output of :meth:`Router.route` — the wing/room call.

    Kept as a tiny frozen dataclass so it can be embedded in event
    payloads (see ``memory.routed``) without dragging pydantic in.
    """

    wing: str
    room: str
    kind_used: str | None
    rule: str  # 'kind-explicit' | 'keyword:<token>' | 'fallback'


class Router:
    """Stateless routing rules + stateful adapter + bus handles."""

    def __init__(
        self,
        *,
        bus: EventBus,
        adapter: MemPalaceAdapter,
        wing: str,
    ) -> None:
        self._bus = bus
        self._adapter = adapter
        self._wing = wing

    # ------------------------------------------------------------ pure rules
    def route(self, env: EventEnvelope) -> RouterDecision:
        """Decide wing/room from a ``raw.captured`` envelope. No I/O.

        Args:
            env: Must be of type :data:`EventType.RAW_CAPTURED`.

        Returns:
            :class:`RouterDecision` with the chosen wing/room and a
            short ``rule`` tag explaining why (for the debug log in
            ``memory.routed``).
        """
        if env.type is not EventType.RAW_CAPTURED:
            raise ValueError(f"router only handles raw.captured envelopes, got {env.type.value!r}")

        kind = env.payload.get("kind")
        if isinstance(kind, str) and kind in KIND_TO_ROOM:
            return RouterDecision(
                wing=self._wing,
                room=_room(KIND_TO_ROOM[kind]),
                kind_used=kind,
                rule="kind-explicit",
            )

        content = env.payload.get("content", "")
        if isinstance(content, str) and content:
            for pattern, inferred_kind in _KEYWORD_RULES:
                m = pattern.search(content)
                if m:
                    return RouterDecision(
                        wing=self._wing,
                        room=_room(KIND_TO_ROOM[inferred_kind]),
                        kind_used=inferred_kind,
                        rule=f"keyword:{m.group(0).lower()}",
                    )

        return RouterDecision(
            wing=self._wing,
            room=_room("general"),
            kind_used=None,
            rule="fallback",
        )

    # -------------------------------------------------------- sync fast-path
    def route_and_store(self, env: EventEnvelope) -> MemPalaceWriteResult:
        """Route + persist + emit ``memory.routed`` and ``memory.stored``.

        Used by :meth:`Memory.remember` for the explicit fast path.

        Args:
            env: A ``raw.captured`` envelope produced by
                :meth:`EventBus.emit`.

        Returns:
            The :class:`MemPalaceWriteResult` from the adapter (or a
            synthesised result echoing a prior drawer when the dedup
            short-circuit fires — see T1.19).

        Raises:
            ValueError: *env* is not ``raw.captured`` or its payload
                lacks usable ``content``.
        """
        decision = self.route(env)
        content = env.payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("raw.captured payload must carry non-empty 'content'")

        # T1.19: cross-producer dedup. If an earlier capture (by *any*
        # producer — explicit, lifecycle hook, pressure hook) already
        # landed the same ``content_hash``, skip the adapter write and
        # surface the prior drawer instead. This is the path that stops
        # an explicit ``remember("decided on X")`` mid-session from
        # being double-stored when SessionEnd later captures the same
        # transcript line.
        c_hash = env.payload.get("content_hash")
        if isinstance(c_hash, str) and c_hash:
            prior = self._find_stored_by_hash(c_hash)
            if prior is not None:
                return self._emit_dedup_skip(env, prior, decision)

        # 1. log the decision (observability)
        self._bus.emit(
            type=EventType.MEMORY_ROUTED,
            source="worker:router",
            payload={
                "raw_event_id": env.id,
                "wing": decision.wing,
                "room": decision.room,
                "kind_used": decision.kind_used,
                "rule": decision.rule,
            },
        )

        # 2. persist
        write_res = self._adapter.write(
            content=content,
            wing=decision.wing,
            room=decision.room,
        )

        # 3. ack — content_hash is mirrored into memory.stored so the
        # dedup scan above stays a single-pass walk over one event type.
        self._bus.emit(
            type=EventType.MEMORY_STORED,
            source="adapter:mempalace",
            payload={
                "drawer_id": write_res.drawer_id,
                "wing": write_res.wing,
                "room": write_res.room,
                "raw_event_id": env.id,
                "content_hash": c_hash,
            },
        )
        return write_res

    # ------------------------------------------------------------- async loop
    async def consume_loop(
        self,
        *,
        ignore_sources: Iterable[str] = ("explicit",),
        poll_interval: float = 0.5,
        stop: asyncio.Event | None = None,
    ) -> None:
        """Poll the bus for unrouted ``raw.captured`` events forever.

        Designed to live inside :class:`WorkerScheduler`. The loop
        skips events whose ``source`` starts with any prefix in
        *ignore_sources* — by default ``explicit`` events have
        already been routed synchronously by :meth:`Memory.remember`,
        so we don't double-process them. Prefix matching means
        ``"explicit"`` skips both ``"explicit"`` and ``"explicit:cli"``.

        Restart-safety story (v0.0.1):

        * On boot we **don't** start from a saved cursor. Instead we
          replay the entire ring window and dedupe per envelope by
          asking "has a ``memory.stored`` event already been emitted
          for *this* raw_event_id?". This makes write failures
          retryable on restart — losing ``memory.routed`` (which is
          only an observability log) doesn't poison the queue.
        * Within a single process lifetime, the cursor advances past
          every event we look at. A transient ``adapter.write`` failure
          is therefore **not** retried in-process today; v0.0.2 adds a
          ``router.failed`` event + retry queue.

        Args:
            ignore_sources: Producer prefixes to skip (default
                ``("explicit",)``).
            poll_interval: Seconds between polls when the bus is idle.
            stop: Optional :class:`asyncio.Event`; setting it makes the
                loop exit cleanly. Without one the loop runs forever
                and is cancelled by the scheduler.
        """
        ignored = tuple(ignore_sources)
        cursor = self._initial_cursor()
        while True:
            if stop is not None and stop.is_set():
                return

            new_events = self._bus.since(
                cursor_id=cursor,
                types=[EventType.RAW_CAPTURED],
                limit=100,
            )
            for env in new_events:
                cursor = env.id
                if any(env.source.startswith(prefix) for prefix in ignored):
                    continue
                # Dedup on *successful persistence*, not on routing.
                # ``memory.routed`` is logged before the adapter write,
                # so using it as the dedup key would silently drop
                # events whose write actually failed.
                if self._already_stored(env.id):
                    continue
                try:
                    self.route_and_store(env)
                except Exception:  # pragma: no cover
                    # TODO(v0.0.2): emit a ``router.failed`` event and
                    # log the traceback. Today we swallow so a single
                    # bad envelope never kills the worker; the original
                    # raw.captured stays in the ring and will retry on
                    # the next process restart (no memory.stored = not
                    # deduped).
                    continue

            try:
                if stop is None:
                    await asyncio.sleep(poll_interval)
                else:
                    # Sleep cooperatively so stop.set() unblocks fast.
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                continue

    # ---------------------------------------------------------------- helpers
    def _initial_cursor(self) -> str | None:
        """Always start from the oldest event in the ring window.

        v0.0.1 simplification: instead of persisting a cursor, we let
        ``_already_stored`` shoulder the dedup work. On restart we
        re-scan the whole ``raw.captured`` window — which is exactly
        what we want when the previous run crashed mid-write (the
        retry path needs that re-scan to even consider the failed
        envelope).
        """
        return None

    def _already_stored(self, raw_event_id: str) -> bool:
        """Has a ``memory.stored`` event been emitted for *raw_event_id*?

        Used as the consume-loop dedup signal. We deliberately key on
        ``memory.stored`` (post-write) rather than ``memory.routed``
        (pre-write) so a failed adapter call doesn't get marked as
        "done" and silently drop the envelope.
        """
        # Bounded scan of recent stored events. Ring is small (default
        # 500) so this is cheap; we widen to 500 to cover the worst
        # case where the whole window is back-to-back stores.
        for env in self._bus.tail(n=500, types=[EventType.MEMORY_STORED]):
            if env.payload.get("raw_event_id") == raw_event_id:
                return True
        return False

    def _find_stored_by_hash(self, c_hash: str) -> EventEnvelope | None:
        """Return the most-recent ``memory.stored`` carrying ``content_hash``.

        T1.19 cross-producer dedup. Walks the ring window of
        ``memory.stored`` events newest-first and returns the first
        match — that's the prior drawer the new capture should
        coalesce against. ``None`` means "no match, do the write".

        Note we walk ``memory.stored``, not ``raw.captured``: a
        ``raw.captured`` whose write failed *will* still be in the ring
        but should NOT count as deduped (otherwise the retry path is
        permanently broken). Mirroring ``content_hash`` into
        ``memory.stored`` payloads at write time gives us a single-pass
        scan that excludes failures by construction.
        """
        for env in self._bus.tail(n=500, types=[EventType.MEMORY_STORED]):
            if env.payload.get("content_hash") == c_hash:
                return env
        return None

    def _emit_dedup_skip(
        self,
        raw_env: EventEnvelope,
        prior_stored: EventEnvelope,
        decision: RouterDecision,
    ) -> MemPalaceWriteResult:
        """Skip the adapter write, log the dedup, return the prior drawer.

        Emits one ``memory.curated`` event (``reason="dedup"``) so
        observability tools can count cross-producer collisions without
        scanning content payloads. Does NOT emit ``memory.routed`` or
        ``memory.stored`` — the prior write already produced those, and
        re-emitting would break the consume-loop's
        ``raw_event_id``-keyed dedup invariant (one stored per raw).

        The synthesised :class:`MemPalaceWriteResult` lets
        :meth:`Memory.remember` hand the caller a stable drawer_id /
        wing / room that points at the *original* drawer — exactly what
        an idempotent ``remember`` should return.
        """
        prior_drawer_id = str(prior_stored.payload.get("drawer_id", ""))
        prior_wing = str(prior_stored.payload.get("wing", decision.wing))
        prior_room = str(prior_stored.payload.get("room", decision.room))

        self._bus.emit(
            type=EventType.MEMORY_CURATED,
            source="worker:router",
            payload={
                "reason": "dedup",
                "raw_event_id": raw_env.id,
                "producer_kind": raw_env.payload.get("producer_kind"),
                "content_hash": raw_env.payload.get("content_hash"),
                "original_stored_event_id": prior_stored.id,
                "drawer_id": prior_drawer_id,
                "wing": prior_wing,
                "room": prior_room,
            },
        )
        return MemPalaceWriteResult(
            drawer_id=prior_drawer_id,
            wing=prior_wing,
            room=prior_room,
        )
