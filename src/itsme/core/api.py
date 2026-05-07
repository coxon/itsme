"""Internal SDK — `Memory` orchestrator (ARCHITECTURE §4, §6).

The MCP tool surface is **thin** — argument validation only.  All real
work happens here: emit events, talk to adapters, return structured
results.  This way the same orchestrator is reachable from MCP tools,
hooks, and tests without a tool-protocol roundtrip.

v0.0.1 contract (matches ROADMAP T1.10–T1.15):

* :meth:`Memory.remember` — fast-path write through the rule-based
  :class:`Router` worker (T1.15). Each call emits ``raw.captured``
  → ``memory.routed`` → ``memory.stored`` synchronously.
* :meth:`Memory.ask` — direct verbatim search; ``mode='auto'`` /
  ``promote=True`` are deferred to v0.0.2 / v0.0.3.
* :meth:`Memory.status` — read events ring.
"""

from __future__ import annotations

from collections.abc import Coroutine, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from itsme.core.adapters import (
    InMemoryMemPalaceAdapter,
    MemPalaceAdapter,
    MemPalaceHit,
)
from itsme.core.adapters.naming import wing as _wing
from itsme.core.aleph.api import Aleph
from itsme.core.aleph.vault import AlephVault
from itsme.core.dedup import content_hash, producer_kind_from_source
from itsme.core.events import EventBus, EventEnvelope, EventType
from itsme.core.llm import LLMProvider, StubProvider, build_llm_provider
from itsme.core.search import SearchHit, dual_search, vault_search
from itsme.core.workers.intake import IntakeProcessor
from itsme.core.workers.router import Router

# All 4 documented modes are part of the type even though only
# ``verbatim`` is implemented in v0.0.1 — the others raise
# :class:`NotImplementedError` at the boundary so the type accepts
# them and the runtime rejects them with a precise message.
AskMode = Literal["verbatim", "auto", "wiki", "now"]
RememberKind = Literal["decision", "fact", "feeling", "todo", "event", "general"]
StatusScope = Literal["recent", "today", "session"]
StatusFormat = Literal["json", "feed"]


class RememberResult(BaseModel):
    """What :meth:`Memory.remember` returns to its caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(description="raw.captured event id")
    drawer_id: str = Field(description="MemPalace drawer id")
    wing: str
    room: str
    routed_to: list[str] = Field(default_factory=list)
    stored_event_id: str


class AskSource(BaseModel):
    """One row of provenance behind :class:`AskResult`."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["verbatim", "wiki", "extraction"]
    ref: str
    content: str
    score: float


class AskResult(BaseModel):
    """What :meth:`Memory.ask` returns. v0.0.1 stitches verbatim hits."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    answer: str
    sources: list[AskSource]
    queried_event_id: str
    promoted: bool = False
    promotion_event_id: str | None = None


class StatusEvent(BaseModel):
    """A flattened, JSON-friendly view of an :class:`EventEnvelope`."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    ts: datetime
    type: str
    source: str
    payload: dict[str, Any]


class StatusResult(BaseModel):
    """:meth:`Memory.status` payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: StatusScope
    count: int
    events: list[StatusEvent]


# --------------------------------------------------------------------------
# Memory orchestrator
# --------------------------------------------------------------------------


class Memory:
    """itsme's in-process facade — the thing MCP tools dispatch to.

    Args:
        bus: An :class:`EventBus` instance (typically singleton per
            process).
        adapter: A :class:`MemPalaceAdapter` implementation. Defaults to
            :class:`InMemoryMemPalaceAdapter` so tests and bare-bones
            development just work.
        project: Project name; becomes the default wing prefix.
        aleph: Optional :class:`Aleph` instance for dual-engine search
            (v0.0.2). When None, ``mode='auto'`` degrades to verbatim.
        llm: Optional :class:`LLMProvider` for intake processing.
            When None, intake degrades to raw MemPalace writes only.
        vault: Optional :class:`AlephVault` for Obsidian wiki integration.
            When provided with a working LLM, intake consolidates kept
            turns into vault wiki pages. Also enables ``ask(mode='wiki')``.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        adapter: MemPalaceAdapter | None = None,
        project: str = "default",
        aleph: Aleph | None = None,
        llm: LLMProvider | None = None,
        vault: AlephVault | None = None,
    ) -> None:
        self._bus = bus
        self._adapter: MemPalaceAdapter = adapter or InMemoryMemPalaceAdapter()
        self._wing = _wing(project)
        self._router = Router(bus=self._bus, adapter=self._adapter, wing=self._wing)
        self._aleph = aleph
        self._llm = llm
        self._vault = vault

        # Build intake processor for hook captures (replaces router
        # consume_loop for non-explicit sources in v0.0.2).
        if self._aleph is not None:
            self._intake = IntakeProcessor(
                bus=self._bus,
                adapter=self._adapter,
                aleph=self._aleph,
                llm=self._llm or StubProvider(),
                wing=self._wing,
                vault=self._vault,
            )
        else:
            self._intake = None

    # ------------------------------------------------------------------ remember
    def remember(
        self,
        content: str,
        kind: RememberKind | None = None,
        *,
        source: str = "explicit",
    ) -> RememberResult:
        """Persist *content* to MemPalace via the router fast-path.

        v0.0.1 sync flow — no LLM. Each call:

        1. emits ``raw.captured``
        2. delegates to :meth:`Router.route_and_store`, which decides
           wing/room (rule-based), emits ``memory.routed``, writes the
           drawer, and emits ``memory.stored``.

        Args:
            content: Verbatim text to store. Empty strings are rejected.
            kind: Optional hint that selects the room. Recognised values
                map 1-to-1 (``decision``, ``fact``, ``feeling``,
                ``todo``, ``event``); unknown values are dropped and
                routing falls back to keyword inference / ``general``.
            source: Producer label written into the event envelope.

        Returns:
            :class:`RememberResult` with the raw event id, drawer id,
            and the secondary ``memory.stored`` event id.

        Raises:
            ValueError: *content* is empty or whitespace-only.
            RuntimeError: ``Router.route_and_store`` succeeded but
                ``_latest_stored_event_id`` couldn't find the matching
                ``memory.stored`` event — indicates an upstream
                contract violation in the router/adapter chain.
        """
        if not content.strip():
            raise ValueError("remember(content=...) must be non-empty")

        raw_evt = self._bus.emit(
            type=EventType.RAW_CAPTURED,
            source=source,
            payload={
                "content": content,
                "kind": kind,
                # T1.19: stamp identity so downstream router can
                # dedup against prior captures (hook + explicit cross
                # the same fact constantly in real CC sessions).
                "content_hash": content_hash(content),
                "producer_kind": producer_kind_from_source(source),
            },
        )

        write_res = self._router.route_and_store(raw_evt)

        # ``route_and_store`` emits ``memory.stored`` last; tail it back
        # so we can hand the caller a stable event id.
        stored_id = self._latest_stored_event_id(raw_evt.id)

        return RememberResult(
            id=raw_evt.id,
            drawer_id=write_res.drawer_id,
            wing=write_res.wing,
            room=write_res.room,
            routed_to=[f"mempalace:{write_res.drawer_id}"],
            stored_event_id=stored_id,
        )

    def _latest_stored_event_id(self, raw_event_id: str) -> str:
        """Find the ``memory.stored`` event matching *raw_event_id*.

        ``Router.route_and_store`` emits exactly one ``memory.stored``
        event per successful write, with ``raw_event_id`` in the
        payload. We scan the full ring window (default 500 entries) so
        concurrent writes from other producers can't push the match
        out of view; if it really isn't there the contract has been
        violated upstream and we raise rather than handing the caller
        an empty / wrong id.

        T1.19 dedup case: when the router short-circuits via
        ``_emit_dedup_skip`` it does NOT emit a fresh ``memory.stored``
        for *raw_event_id* — instead it emits a ``memory.curated``
        whose payload carries ``original_stored_event_id`` pointing at
        the prior drawer's stored event. We honour that link so the
        caller still gets a stable id corresponding to a real drawer
        write (just the original one, not a duplicate).

        Raises:
            RuntimeError: No matching ``memory.stored`` was found and no
                ``memory.curated`` dedup link either — indicates
                ``Router.route_and_store`` returned without emitting
                either event, which is a bug.
        """
        # Use the bus's full ring capacity (default 500). The router
        # emits memory.stored last, so a tail walk is O(window).
        for env in self._bus.tail(n=self._bus.count(), types=[EventType.MEMORY_STORED]):
            if env.payload.get("raw_event_id") == raw_event_id:
                return env.id
        # Dedup fallback — find the curated event that points back at
        # the prior drawer's stored event id and return *that*.
        for env in self._bus.tail(n=self._bus.count(), types=[EventType.MEMORY_CURATED]):
            if (
                env.payload.get("raw_event_id") == raw_event_id
                and env.payload.get("reason") == "dedup"
            ):
                original = env.payload.get("original_stored_event_id")
                if isinstance(original, str) and original:
                    return original
        raise RuntimeError(f"router did not emit memory.stored for raw_event_id={raw_event_id!r}")

    # ------------------------------------------------------------------ ask
    def ask(
        self,
        question: str,
        *,
        mode: AskMode = "verbatim",
        limit: int = 5,
        scope_to_project: bool = True,
    ) -> AskResult:
        """Query memory and emit ``memory.queried``.

        Supports three modes:

        * ``verbatim`` — MemPalace-only keyword search (v0.0.1 behavior).
        * ``auto`` — triple-engine: Vault wiki (consolidated knowledge)
          + Aleph extraction index (high precision) + MemPalace raw
          (high recall), merged and deduplicated.
        * ``wiki`` — Vault wiki pages only (Obsidian vault search).

        ``now`` mode is deferred to v0.0.4+.

        Args:
            question: Natural-language query.
            mode: Read strategy.
            limit: Max number of hits to return.
            scope_to_project: When True, restrict the MemPalace search
                to the project's wing; when False, search across all wings.

        Returns:
            :class:`AskResult` with a stitched answer and provenance
            sources.

        Raises:
            ValueError: *question* is empty or *limit* is non-positive.
            NotImplementedError: a mode not yet implemented was passed.
        """
        if not question.strip():
            raise ValueError("ask(question=...) must be non-empty")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if mode not in ("verbatim", "auto", "wiki"):
            raise NotImplementedError(
                f"mode={mode!r} is not implemented — "
                "only 'verbatim', 'auto', and 'wiki' are supported"
            )

        wing_filter = self._wing if scope_to_project else None

        if mode == "wiki":
            return self._ask_wiki(question, limit=limit)
        if mode == "auto":
            return self._ask_auto(question, wing_filter=wing_filter, limit=limit)
        return self._ask_verbatim(question, wing_filter=wing_filter, limit=limit)

    def _ask_verbatim(
        self,
        question: str,
        *,
        wing_filter: str | None,
        limit: int,
    ) -> AskResult:
        """MemPalace-only search (v0.0.1 behavior)."""
        hits: list[MemPalaceHit] = self._adapter.search(
            question,
            limit=limit,
            wing=wing_filter,
        )

        evt = self._bus.emit(
            type=EventType.MEMORY_QUERIED,
            source="reader",
            payload={
                "question": question,
                "mode": "verbatim",
                "hit_count": len(hits),
                "wing": wing_filter,
            },
        )

        sources = [
            AskSource(
                kind="verbatim",
                ref=f"mempalace:{h.drawer_id}",
                content=h.content,
                score=h.score,
            )
            for h in hits
        ]
        return AskResult(
            answer=_stitch_answer(hits),
            sources=sources,
            queried_event_id=evt.id,
            promoted=False,
            promotion_event_id=None,
        )

    def _ask_auto(
        self,
        question: str,
        *,
        wing_filter: str | None,
        limit: int,
    ) -> AskResult:
        """Triple-engine search: Vault wiki + Aleph + MemPalace.

        When Aleph or vault is not wired (None), gracefully degrades.
        """
        hits = dual_search(
            question,
            adapter=self._adapter,
            aleph=self._aleph,
            vault=self._vault,
            wing=wing_filter,
            limit=limit,
        )

        wiki_hits = sum(1 for h in hits if h.kind == "wiki")
        evt = self._bus.emit(
            type=EventType.MEMORY_QUERIED,
            source="reader",
            payload={
                "question": question,
                "mode": "auto",
                "hit_count": len(hits),
                "wiki_hits": wiki_hits,
                "aleph_hits": sum(1 for h in hits if h.kind == "extraction"),
                "mp_hits": sum(1 for h in hits if h.kind == "verbatim"),
                "wing": wing_filter,
            },
        )

        sources = [
            AskSource(
                kind=h.kind,  # type: ignore[arg-type]
                ref=h.ref,
                content=h.content,
                score=h.score,
            )
            for h in hits
        ]
        return AskResult(
            answer=_stitch_auto_answer(hits),
            sources=sources,
            queried_event_id=evt.id,
            promoted=False,
            promotion_event_id=None,
        )

    def _ask_wiki(
        self,
        question: str,
        *,
        limit: int,
    ) -> AskResult:
        """Vault wiki page search only.

        When vault is not wired, returns empty results (no error).
        """
        if self._vault is None:
            hits: list[SearchHit] = []
        else:
            hits = vault_search(question, vault=self._vault, limit=limit)

        evt = self._bus.emit(
            type=EventType.MEMORY_QUERIED,
            source="reader",
            payload={
                "question": question,
                "mode": "wiki",
                "hit_count": len(hits),
            },
        )

        sources = [
            AskSource(
                kind="wiki",
                ref=h.ref,
                content=h.content,
                score=h.score,
            )
            for h in hits
        ]
        return AskResult(
            answer=_stitch_auto_answer(hits),
            sources=sources,
            queried_event_id=evt.id,
            promoted=False,
            promotion_event_id=None,
        )

    # ------------------------------------------------------------------ status
    def status(
        self,
        *,
        scope: StatusScope = "recent",
        limit: int = 20,
        types: Iterable[EventType] | None = None,
    ) -> StatusResult:
        """Surface recent activity from the events ring.

        Args:
            scope: ``recent`` returns the latest *limit* events;
                ``today`` filters to events whose ts is within the last
                24h; ``session`` is treated as ``recent`` until session
                tracking exists (v0.0.3+).
            limit: Max events to return.
            types: Optional event-type filter.

        Returns:
            :class:`StatusResult` with the matching events newest-first.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")

        events: list[EventEnvelope]
        if scope == "today":
            cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
            events = [e for e in self._bus.tail(n=max(limit, 100), types=types) if e.ts >= cutoff][
                :limit
            ]
        else:
            # 'recent' and 'session' both fall back to the tail until
            # session tracking is wired up.
            events = self._bus.tail(n=limit, types=types)

        flat = [
            StatusEvent(
                id=e.id,
                ts=e.ts,
                type=e.type.value,
                source=e.source,
                payload=dict(e.payload),
            )
            for e in events
        ]
        return StatusResult(scope=scope, count=len(flat), events=flat)

    # ------------------------------------------------------------------ lifecycle
    def consume_loop(
        self,
        *,
        ignore_sources: Iterable[str] = ("explicit",),
        poll_interval: float = 0.5,
    ) -> Coroutine[Any, Any, None]:
        """Return the background consume loop coroutine.

        v0.0.2: when an :class:`IntakeProcessor` is wired (Aleph +
        optional LLM), returns the intake loop — which groups by
        ``capture_batch_id``, runs LLM extraction, and dual-writes to
        MemPalace + Aleph.

        Fallback (no Aleph): returns the router's consume loop
        (v0.0.1 behavior — rule-based routing, MemPalace only).

        Used by ``itsme.mcp.server`` to register a background worker
        with the :class:`WorkerScheduler`.
        """
        if self._intake is not None:
            return self._intake.consume_loop(
                ignore_sources=ignore_sources,
                poll_interval=poll_interval,
            )
        return self._router.consume_loop(
            ignore_sources=ignore_sources,
            poll_interval=poll_interval,
        )

    def close(self) -> None:
        """Close the bus, adapter, and Aleph. Safe to call multiple times."""
        self._adapter.close()
        self._bus.close()
        if self._aleph is not None:
            self._aleph.close()
        # AlephVault has no close — it's just a path wrapper


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _stitch_answer(hits: list[MemPalaceHit]) -> str:
    """v0.0.1 placeholder — concatenate verbatim hits with rules.

    The architecture calls for LLM fusion in ``ask(promote=True)``; that
    arrives in v0.0.3. Until then we return the raw passages so the
    caller (a coding agent) can do its own synthesis.
    """
    if not hits:
        return ""
    parts = [f"[{h.score:.2f}] {h.content}" for h in hits]
    return "\n\n---\n\n".join(parts)


def _stitch_auto_answer(hits: list[SearchHit]) -> str:
    """Concatenate dual-engine search results with kind labels.

    Aleph hits show as ``[extraction 0.85]`` and MemPalace hits as
    ``[verbatim 0.72]`` so the caller can distinguish precision
    vs recall sources at a glance.
    """
    if not hits:
        return ""
    parts: list[str] = []
    for h in hits:
        parts.append(f"[{h.kind} {h.score:.2f}] {h.content}")
    return "\n\n---\n\n".join(parts)


def default_db_path() -> Path:
    """Default events ring location — ``~/.itsme/events.db``."""
    return Path.home() / ".itsme" / "events.db"


def build_default_memory(
    *,
    project: str = "default",
    db_path: Path | None = None,
    capacity: int = 500,
    adapter: MemPalaceAdapter | None = None,
    aleph: Aleph | None = None,
    llm: LLMProvider | None = None,
    vault: AlephVault | None = None,
) -> Memory:
    """Construct a :class:`Memory` with sensible defaults.

    Used by ``itsme.mcp.server`` to wire up a Memory instance from
    config without leaking pydantic / sqlite plumbing into the MCP
    layer.

    If *aleph* is not passed, an :class:`Aleph` instance is created
    at the default path (``~/.itsme/aleph.db`` or ``$ITSME_ALEPH_DB``).
    This enables ``ask(mode='auto')`` out of the box.

    If *llm* is not passed, :func:`build_llm_provider` is called to
    auto-detect from ``$DEEPSEEK_API_KEY``. If no key is set, intake
    runs in degraded mode (raw writes only, no extraction).

    If *vault* is not passed, auto-discovers the Obsidian Aleph vault
    at ``$ITSME_ALEPH_VAULT`` or ``~/Documents/Aleph/``. When found,
    enables vault wiki consolidation during intake and
    ``ask(mode='wiki')`` search.

    Backend selection (when *adapter* is not passed) keys off
    ``$ITSME_MEMPALACE_BACKEND``:

    * ``auto`` (**default**) → try ``stdio``; on
      :class:`~itsme.core.adapters.MemPalaceConnectError` fall back to
      ``inmemory`` with a ``stderr`` warning.
    * ``stdio`` → spawn a real MemPalace MCP server via
      :class:`StdioMemPalaceAdapter`.
    * ``inmemory`` → in-process
      :class:`InMemoryMemPalaceAdapter`.
    """
    import sys

    bus = EventBus(db_path=db_path or default_db_path(), capacity=capacity)
    if adapter is None:
        adapter = _select_mempalace_backend()
    if aleph is None:
        aleph = Aleph()  # uses default path
    if llm is None:
        llm = build_llm_provider()
        if llm is None:
            print(
                "itsme: no DEEPSEEK_API_KEY set — intake runs in degraded mode "
                "(raw MemPalace writes only, no Aleph extraction). "
                "Set DEEPSEEK_API_KEY to enable LLM intake.",
                file=sys.stderr,
            )
    if vault is None:
        vault = _discover_vault()
    return Memory(
        bus=bus,
        adapter=adapter,
        project=project,
        aleph=aleph,
        llm=llm,
        vault=vault,
    )


def _select_mempalace_backend() -> MemPalaceAdapter:
    """Pick a MemPalace backend based on ``$ITSME_MEMPALACE_BACKEND``.

    Kept as a separate helper so tests can monkeypatch the env var and
    re-call ``build_default_memory`` without reaching into module state.
    """
    import os
    import sys

    backend = os.environ.get("ITSME_MEMPALACE_BACKEND", "auto").strip().lower()

    if backend == "" or backend == "auto":
        # Lazy import: don't pay the subprocess-adapter import cost for
        # callers that explicitly opt out (``inmemory``).
        from itsme.core.adapters.mempalace_stdio import (
            MemPalaceConnectError,
            StdioMemPalaceAdapter,
        )

        try:
            return StdioMemPalaceAdapter.from_env()
        except MemPalaceConnectError as exc:
            print(
                f"itsme: MemPalace stdio backend unavailable ({exc}); "
                "falling back to in-memory adapter — drawers will not "
                "persist across MCP server restarts. Install mempalace "
                "(or set ITSME_MEMPALACE_BACKEND=inmemory to silence) to fix.",
                file=sys.stderr,
            )
            return InMemoryMemPalaceAdapter()

    if backend == "inmemory":
        return InMemoryMemPalaceAdapter()

    if backend == "stdio":
        from itsme.core.adapters.mempalace_stdio import StdioMemPalaceAdapter

        return StdioMemPalaceAdapter.from_env()

    # Unknown value → refuse silently would hide typos; loud is better.
    raise ValueError(
        f"unknown ITSME_MEMPALACE_BACKEND={backend!r} " "(expected one of: auto, inmemory, stdio)"
    )


def _discover_vault() -> AlephVault | None:
    """Auto-discover the Obsidian Aleph vault.

    Checks ``$ITSME_ALEPH_VAULT`` first, then falls back to
    ``~/Documents/Aleph/``. Returns None if no vault is found
    (vault integration is optional — just means no wiki writes or
    wiki search).
    """
    import os
    import sys

    env_path = os.environ.get("ITSME_ALEPH_VAULT", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(Path.home() / "Documents" / "Aleph")

    for candidate in candidates:
        if (candidate / "dna.md").exists():
            try:
                vault = AlephVault(candidate)
                print(f"itsme: Aleph vault discovered at {candidate}", file=sys.stderr)
                return vault
            except Exception as exc:
                print(
                    f"itsme: Aleph vault found at {candidate} but failed to open: {exc}",
                    file=sys.stderr,
                )
                return None

    return None
