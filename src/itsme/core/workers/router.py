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
import contextlib
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
            The :class:`MemPalaceWriteResult` from the adapter.

        Raises:
            ValueError: *env* is not ``raw.captured`` or its payload
                lacks usable ``content``.
        """
        decision = self.route(env)
        content = env.payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("raw.captured payload must carry non-empty 'content'")

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

        # 3. ack
        self._bus.emit(
            type=EventType.MEMORY_STORED,
            source="adapter:mempalace",
            payload={
                "drawer_id": write_res.drawer_id,
                "wing": write_res.wing,
                "room": write_res.room,
                "raw_event_id": env.id,
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
        skips events whose source is in *ignore_sources* — by default
        ``explicit`` events have already been routed synchronously by
        :meth:`Memory.remember`, so we don't double-process them.

        The loop tracks already-routed envelopes by walking the
        ``memory.routed`` events on startup, then advancing a cursor
        as it consumes. This makes restart safe: a new server doesn't
        re-route old hook events.

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
                # Defensive: a router-poll after restart might re-see
                # already-routed events. ``_already_routed`` walks
                # memory.routed payloads to dedupe.
                if self._already_routed(env.id):
                    continue
                # Never kill the loop. In v0.0.1 we just swallow;
                # v0.0.2 will emit a router.failed event with the
                # traceback for observability.
                with contextlib.suppress(Exception):  # pragma: no cover
                    self.route_and_store(env)

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
        """Resume cursor — newest already-routed raw.captured id, or None."""
        latest = self._bus.tail(n=1, types=[EventType.MEMORY_ROUTED])
        if not latest:
            return None
        raw_id = latest[0].payload.get("raw_event_id")
        return raw_id if isinstance(raw_id, str) else None

    def _already_routed(self, raw_event_id: str) -> bool:
        """Has a ``memory.routed`` event been emitted for *raw_event_id*?"""
        # Bounded scan of recent routed events. The ring is small
        # (default 500) so this is cheap.
        for env in self._bus.tail(n=200, types=[EventType.MEMORY_ROUTED]):
            if env.payload.get("raw_event_id") == raw_event_id:
                return True
        return False
