"""Intake worker — T2.0d + T3.0 (SQLite FTS5 removed).

Processes hook-captured ``raw.captured`` events through the LLM intake
pipeline:

1. Groups per-turn events by ``capture_batch_id``
2. Sends the batch to the LLM for extraction
3. Writes ALL turns to MemPalace (raw, full recall)
4. Feeds KEEP turns to AlephRound for wiki consolidation
5. Emits ``raw.triaged`` for observability

The intake worker replaces the router's ``consume_loop`` for hook
captures. Explicit ``remember()`` calls still go through the router's
synchronous fast-path and are NOT processed by intake.

LLM degradation: if the LLM is unavailable (no API key, network error),
turns are written to MemPalace as raw (v0.0.1 behavior) without wiki
consolidation. No data loss, just lower search precision.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files as _files
from typing import Any

from itsme.core.adapters import MemPalaceAdapter
from itsme.core.adapters.naming import WIKI_ROOM, WIKI_WING
from itsme.core.adapters.naming import room as _room
from itsme.core.aleph.round import AlephRound, RoundResult, TurnContent
from itsme.core.aleph.wiki import Aleph
from itsme.core.events import EventBus, EventEnvelope, EventType
from itsme.core.llm import LLMProvider, LLMUnavailableError, StubProvider
from itsme.core.workers.curator import Curator

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- prompt

_INTAKE_PROMPT: str | None = None


def _load_intake_prompt() -> str:
    """Load the intake system prompt from the bundled markdown file."""
    global _INTAKE_PROMPT  # noqa: PLW0603
    if _INTAKE_PROMPT is None:
        prompt_file = _files("itsme.core.aleph.prompts").joinpath("intake.md")
        _INTAKE_PROMPT = prompt_file.read_text(encoding="utf-8")
    return _INTAKE_PROMPT


# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class IntakeResult:
    """Result of processing one turn through the intake pipeline."""

    turn_event_id: str
    verdict: str  # "keep" | "skip" | "error"
    summary: str
    entities: list[dict[str, str]]
    claims: list[str]
    skip_reason: str
    drawer_id: str  # MemPalace drawer id (always written)


# --------------------------------------------------------------------- core


class IntakeProcessor:
    """Processes raw.captured turn events through LLM extraction.

    Args:
        bus: EventBus for emitting triaged events.
        adapter: MemPalace adapter for raw writes.
        llm: LLM provider for extraction. StubProvider = degraded mode.
        wing: Wing prefix for MemPalace writes.
        aleph: Optional :class:`Aleph` for wiki consolidation. When
            provided (with a working LLM), kept turns are consolidated
            into wiki pages via AlephRound after each batch.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        adapter: MemPalaceAdapter,
        llm: LLMProvider,
        wing: str,
        degraded: bool | None = None,
        aleph: Aleph | None = None,
    ) -> None:
        self._bus = bus
        self._adapter = adapter
        self._llm = llm
        self._wing = wing
        self._aleph = aleph
        # Auto-detect degraded mode: a bare StubProvider (empty response)
        # means no real LLM is available.  A StubProvider with a canned
        # response is used by tests to simulate a working LLM.
        if degraded is not None:
            self._degraded = degraded
        else:
            self._degraded = isinstance(llm, StubProvider) and not llm._response

        # Build AlephRound if Aleph + working LLM are both available
        if aleph is not None and not self._degraded:
            self._round: AlephRound | None = AlephRound(aleph=aleph, llm=llm)
        else:
            self._round = None

        # Build Curator for post-round wiki maintenance
        if aleph is not None:
            self._curator: Curator | None = Curator(aleph=aleph, bus=bus)
        else:
            self._curator = None

    def process_batch(self, events: list[EventEnvelope]) -> list[IntakeResult]:
        """Process a batch of per-turn raw.captured events.

        All turns are written to MemPalace regardless of LLM verdict.
        KEEP turns are fed to AlephRound for wiki consolidation
        (if Aleph is configured).

        Args:
            events: List of ``raw.captured`` events from the same
                ``capture_batch_id``. Each must have ``content`` and
                ``turn_role`` in its payload.

        Returns:
            List of :class:`IntakeResult`, one per input event.
        """
        if not events:
            return []

        # Step 1: Extract structured data via LLM (or degrade)
        extractions = self._extract(events)

        # Step 2: Write all turns to MemPalace, emit triaged
        results: list[IntakeResult] = []
        for event, extraction in zip(events, extractions, strict=False):
            result = self._write_and_emit(event, extraction)
            results.append(result)

        # Step 3: Consolidate kept turns into wiki pages
        # Only include turns that were kept AND successfully written
        round_result = self._run_wiki_round(events, results)
        if round_result is not None:
            self._emit_wiki_events(round_result)
            # Step 4: Sync affected wiki pages to MemPalace for embedding search
            self._sync_wiki_embeddings(round_result.slugs_affected)
            # Step 5: Post-round curator — crosslink + refresh
            self._run_curator()

        return results

    def _extract(self, events: list[EventEnvelope]) -> list[dict[str, Any]]:
        """Call LLM to extract structured data from turns.

        Returns one dict per event with keys: verdict, summary, entities,
        claims, skip_reason. On LLM failure, returns skip-all with
        reason "llm_unavailable".
        """
        if self._degraded:
            return [
                {
                    "verdict": "skip",
                    "skip_reason": "llm_unavailable",
                    "summary": "",
                    "entities": [],
                    "claims": [],
                }
                for _ in events
            ]

        # Build user message: numbered turns
        turn_texts: list[str] = []
        for i, ev in enumerate(events):
            role = ev.payload.get("turn_role", "unknown")
            content = ev.payload.get("content", "")
            turn_texts.append(f"Turn {i + 1} [{role}]:\n{content}")

        user_message = "\n\n---\n\n".join(turn_texts)

        try:
            raw_response = self._llm.complete(
                system=_load_intake_prompt(),
                messages=[{"role": "user", "content": user_message}],
            )
            return _parse_intake_response(raw_response, expected_count=len(events))
        except LLMUnavailableError as exc:
            _logger.warning("itsme intake: LLM unavailable, degrading: %s", exc)
            return [
                {
                    "verdict": "skip",
                    "skip_reason": "llm_unavailable",
                    "summary": "",
                    "entities": [],
                    "claims": [],
                }
                for _ in events
            ]
        except Exception as exc:
            _logger.error("itsme intake: LLM extraction failed: %s", exc)
            return [
                {
                    "verdict": "skip",
                    "skip_reason": f"llm_error: {exc}",
                    "summary": "",
                    "entities": [],
                    "claims": [],
                }
                for _ in events
            ]

    def _write_and_emit(self, event: EventEnvelope, extraction: dict[str, Any]) -> IntakeResult:
        """Write to MemPalace (always) + emit triaged event."""
        content = event.payload.get("content", "")
        turn_role = event.payload.get("turn_role", "unknown")
        verdict = extraction.get("verdict", "skip")
        summary = extraction.get("summary", "")
        entities = extraction.get("entities", [])
        claims = extraction.get("claims", [])
        skip_reason = extraction.get("skip_reason", "")

        # Route to a room based on turn_role
        room = _room("user_turns" if turn_role == "user" else "assistant_turns")

        # Always write raw to MemPalace
        try:
            write_res = self._adapter.write(
                content=content,
                wing=self._wing,
                room=room,
            )
            drawer_id = write_res.drawer_id
        except Exception as exc:
            _logger.error("itsme intake: MemPalace write failed: %s", exc)
            drawer_id = ""

        if drawer_id:
            self._bus.emit(
                type=EventType.MEMORY_STORED,
                source="worker:intake",
                payload={
                    "drawer_id": drawer_id,
                    "wing": self._wing,
                    "room": room,
                    "raw_event_id": event.id,
                    "content_hash": event.payload.get("content_hash"),
                },
            )

        # Emit triaged event for observability
        self._bus.emit(
            type=EventType.MEMORY_ROUTED,
            source="worker:intake",
            payload={
                "raw_event_id": event.id,
                "verdict": verdict,
                "summary": summary[:200] if summary else "",
                "entity_count": len(entities),
                "claim_count": len(claims),
                "skip_reason": skip_reason,
                "drawer_id": drawer_id,
                "wing": self._wing,
                "room": room,
            },
        )

        return IntakeResult(
            turn_event_id=event.id,
            verdict=verdict,
            summary=summary,
            entities=entities,
            claims=claims,
            skip_reason=skip_reason,
            drawer_id=drawer_id,
        )

    # ---------------------------------------------------------- wiki round

    def _run_wiki_round(
        self,
        events: list[EventEnvelope],
        results: list[IntakeResult],
    ) -> RoundResult | None:
        """Feed successfully-kept turns to AlephRound for wiki consolidation.

        Only includes turns that were kept by the LLM AND successfully
        written to MemPalace (have a drawer_id). This prevents orphan
        wiki entries for turns that failed to write.

        Returns the RoundResult, or None if Aleph is not configured or
        no turns qualify.
        """
        if self._round is None:
            return None

        # Collect kept turns that were successfully written
        kept_turns: list[TurnContent] = []
        for event, result in zip(events, results, strict=False):
            if result.verdict != "keep" or not result.drawer_id:
                continue
            role = event.payload.get("turn_role", "user")
            content = event.payload.get("content", "")
            if content:
                kept_turns.append(
                    TurnContent(role=role, content=content, drawer_id=result.drawer_id)
                )

        if not kept_turns:
            return None

        try:
            return self._round.process(kept_turns)
        except Exception as exc:
            _logger.error("itsme intake: wiki round failed: %s", exc)
            return None

    def _emit_wiki_events(self, round_result: RoundResult) -> None:
        """Emit wiki.promoted events for wiki page operations."""
        if round_result.pages_created > 0 or round_result.pages_updated > 0:
            self._bus.emit(
                type=EventType.WIKI_PROMOTED,
                source="worker:intake:wiki-round",
                payload={
                    "pages_created": round_result.pages_created,
                    "pages_updated": round_result.pages_updated,
                    "pages_skipped": round_result.pages_skipped,
                    "errors": round_result.errors,
                },
            )

    # ---------------------------------------------------------- wiki embeddings

    def _run_curator(self) -> None:
        """Run post-round curator for crosslink + refresh.

        Called after each successful wiki round. Errors are logged
        but never block the intake pipeline.
        """
        if self._curator is None:
            return
        try:
            result = self._curator.run()
            if result.total_changes:
                _logger.info(
                    "itsme intake: curator made %d changes post-round",
                    result.total_changes,
                )
        except Exception as exc:
            _logger.error("itsme intake: curator failed: %s", exc)

    def _sync_wiki_embeddings(self, slugs: list[str]) -> None:
        """Write affected wiki pages to MemPalace for embedding search.

        Each page is written to the well-known ``aleph`` wing with
        ``room_wiki`` room so that ``dual_search`` can query them
        separately from raw conversation turns.

        Content format: ``title\\nsummary\\n\\nbody`` — gives the
        embedding model full context for semantic matching.
        """
        if not slugs or self._aleph is None:
            return

        for slug in slugs:
            try:
                meta = self._aleph.find_page(slug)
                if meta is None:
                    continue
                _, body = self._aleph.read_page(meta.path)
                content = _wiki_page_for_embedding(meta.title, meta.summary, body)
                self._adapter.write(
                    content=content,
                    wing=WIKI_WING,
                    room=WIKI_ROOM,
                    source_file=f"wiki:{slug}",
                )
            except Exception as exc:
                _logger.warning("itsme intake: wiki embedding sync failed for %s: %s", slug, exc)

    def sync_all_wiki_pages(self) -> int:
        """Sync ALL wiki pages to MemPalace for embedding search.

        Called once at startup to bootstrap the embedding index for
        existing pages. Returns the number of pages synced.
        """
        if self._aleph is None:
            return 0

        pages = self._aleph.list_pages()
        synced = 0
        for meta in pages:
            try:
                _, body = self._aleph.read_page(meta.path)
                content = _wiki_page_for_embedding(meta.title, meta.summary, body)
                self._adapter.write(
                    content=content,
                    wing=WIKI_WING,
                    room=WIKI_ROOM,
                    source_file=f"wiki:{meta.path.stem}",
                )
                synced += 1
            except Exception as exc:
                _logger.warning(
                    "itsme intake: wiki embedding sync failed for %s: %s", meta.path.stem, exc
                )
        if synced:
            _logger.info("itsme intake: synced %d wiki pages to MemPalace for embedding", synced)
        return synced

    # ---------------------------------------------------------- async loop

    async def consume_loop(
        self,
        *,
        ignore_sources: Iterable[str] = ("explicit",),
        poll_interval: float = 0.5,
        stop: asyncio.Event | None = None,
    ) -> None:
        """Poll the bus for unrouted ``raw.captured`` and process in batches.

        Replaces the router's consume_loop for hook captures. Groups
        events by ``capture_batch_id`` and processes each group as a
        batch through the LLM intake pipeline.

        Events from sources starting with any prefix in *ignore_sources*
        are skipped (default: ``"explicit"`` — those go through
        Memory.remember's sync fast-path).

        Args:
            ignore_sources: Producer prefixes to skip.
            poll_interval: Seconds between polls when idle.
            stop: Optional event; setting it exits the loop.
        """
        ignored = tuple(ignore_sources)
        cursor: str | None = None

        while True:
            if stop is not None and stop.is_set():
                return

            new_events = self._bus.since(
                cursor_id=cursor,
                types=[EventType.RAW_CAPTURED],
                limit=100,
            )

            # Group by capture_batch_id for batch processing
            batches: dict[str, list[EventEnvelope]] = defaultdict(list)
            unbatched: list[EventEnvelope] = []

            for env in new_events:
                cursor = env.id
                if any(env.source.startswith(prefix) for prefix in ignored):
                    continue
                if self._already_stored(env.id):
                    continue

                batch_id = env.payload.get("capture_batch_id")
                if isinstance(batch_id, str) and batch_id:
                    batches[batch_id].append(env)
                else:
                    unbatched.append(env)

            # Process each batch
            for batch_events in batches.values():
                try:
                    self.process_batch(batch_events)
                except Exception as exc:
                    _logger.error("itsme intake: batch processing failed: %s", exc)

            # Process unbatched events individually
            for env in unbatched:
                try:
                    self.process_batch([env])
                except Exception as exc:
                    _logger.error("itsme intake: single event processing failed: %s", exc)

            try:
                if stop is None:
                    await asyncio.sleep(poll_interval)
                else:
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                continue

    def _already_stored(self, raw_event_id: str) -> bool:
        """Has a ``memory.stored`` event been emitted for *raw_event_id*?"""
        for env in self._bus.tail(n=500, types=[EventType.MEMORY_STORED]):
            if env.payload.get("raw_event_id") == raw_event_id:
                return True
        return False


# --------------------------------------------------------------------- wiki embedding


def _wiki_page_for_embedding(title: str, summary: str, body: str) -> str:
    """Format a wiki page for embedding storage.

    Concatenates title, summary, and body so the embedding model gets
    full context for semantic matching. A query like "谁管产品" can match
    a page titled "海龙" with body "产品负责人" via embedding similarity.
    """
    parts = [title]
    if summary:
        parts.append(summary)
    if body:
        parts.append(body)
    return "\n\n".join(parts)


# --------------------------------------------------------------------- parsing


def _parse_intake_response(raw: str, *, expected_count: int) -> list[dict[str, Any]]:
    """Parse the LLM's JSON array response.

    Handles common LLM quirks:
    - Markdown code fences around JSON
    - Truncated arrays (pad with skip entries)
    - Extra entries (truncate to expected_count)
    """
    text = raw.strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        _logger.warning("itsme intake: LLM returned non-JSON, degrading all turns")
        return [
            {
                "verdict": "skip",
                "skip_reason": "llm_parse_error",
                "summary": "",
                "entities": [],
                "claims": [],
            }
        ] * expected_count

    if not isinstance(data, list):
        _logger.warning("itsme intake: LLM returned non-array JSON, degrading")
        return [
            {
                "verdict": "skip",
                "skip_reason": "llm_parse_error",
                "summary": "",
                "entities": [],
                "claims": [],
            }
        ] * expected_count

    # Normalize each entry
    result: list[dict[str, Any]] = []
    for item in data[:expected_count]:
        if not isinstance(item, dict):
            result.append(
                {
                    "verdict": "skip",
                    "skip_reason": "malformed_entry",
                    "summary": "",
                    "entities": [],
                    "claims": [],
                }
            )
            continue
        result.append(
            {
                "verdict": item.get("verdict", "skip"),
                "summary": item.get("summary", ""),
                "entities": item.get("entities", []),
                "claims": item.get("claims", []),
                "skip_reason": item.get("skip_reason", ""),
            }
        )

    # Pad if LLM returned fewer than expected
    while len(result) < expected_count:
        result.append(
            {
                "verdict": "skip",
                "skip_reason": "llm_truncated",
                "summary": "",
                "entities": [],
                "claims": [],
            }
        )

    return result
