"""Intake worker — T2.0d.

Processes hook-captured ``raw.captured`` events through the LLM intake
pipeline:

1. Groups per-turn events by ``capture_batch_id``
2. Sends the batch to the LLM (Haiku) for extraction
3. Writes ALL turns to MemPalace (raw, full recall)
4. Writes KEEP turns to Aleph extraction index (structured, high precision)
5. Emits ``raw.triaged`` for observability

The intake worker replaces the router's ``consume_loop`` for hook
captures. Explicit ``remember()`` calls still go through the router's
synchronous fast-path and are NOT processed by intake.

LLM degradation: if the LLM is unavailable (no API key, network error),
turns are written to MemPalace as raw (v0.0.1 behavior) without Aleph
extraction. No data loss, just lower search precision.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib.resources import files as _files
from typing import Any

from itsme.core.adapters import MemPalaceAdapter
from itsme.core.adapters.naming import room as _room
from itsme.core.aleph.api import Aleph
from itsme.core.events import EventBus, EventEnvelope, EventType
from itsme.core.llm import LLMProvider, LLMUnavailableError, StubProvider

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
    extraction_id: str  # Aleph extraction id (empty if skip/error)


# --------------------------------------------------------------------- core


class IntakeProcessor:
    """Processes raw.captured turn events through LLM extraction.

    Args:
        bus: EventBus for emitting triaged events.
        adapter: MemPalace adapter for raw writes.
        aleph: Aleph SDK for extraction writes.
        llm: LLM provider for extraction. StubProvider = degraded mode.
        wing: Wing prefix for MemPalace writes.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        adapter: MemPalaceAdapter,
        aleph: Aleph,
        llm: LLMProvider,
        wing: str,
        degraded: bool | None = None,
    ) -> None:
        self._bus = bus
        self._adapter = adapter
        self._aleph = aleph
        self._llm = llm
        self._wing = wing
        # Auto-detect degraded mode: a bare StubProvider (empty response)
        # means no real LLM is available.  A StubProvider with a canned
        # response is used by tests to simulate a working LLM.
        if degraded is not None:
            self._degraded = degraded
        else:
            self._degraded = isinstance(llm, StubProvider) and not llm._response

    def process_batch(self, events: list[EventEnvelope]) -> list[IntakeResult]:
        """Process a batch of per-turn raw.captured events.

        All turns are written to MemPalace regardless of LLM verdict.
        KEEP turns additionally get Aleph extraction entries.

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

        # Step 2: Write all turns to MemPalace + Aleph, emit triaged
        results: list[IntakeResult] = []
        for event, extraction in zip(events, extractions):
            result = self._write_and_emit(event, extraction)
            results.append(result)

        return results

    def _extract(self, events: list[EventEnvelope]) -> list[dict[str, Any]]:
        """Call LLM to extract structured data from turns.

        Returns one dict per event with keys: verdict, summary, entities,
        claims, skip_reason. On LLM failure, returns skip-all with
        reason "llm_unavailable".
        """
        if self._degraded:
            return [
                {"verdict": "skip", "skip_reason": "llm_unavailable",
                 "summary": "", "entities": [], "claims": []}
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
                {"verdict": "skip", "skip_reason": "llm_unavailable",
                 "summary": "", "entities": [], "claims": []}
                for _ in events
            ]
        except Exception as exc:
            _logger.error("itsme intake: LLM extraction failed: %s", exc)
            return [
                {"verdict": "skip", "skip_reason": f"llm_error: {exc}",
                 "summary": "", "entities": [], "claims": []}
                for _ in events
            ]

    def _write_and_emit(
        self, event: EventEnvelope, extraction: dict[str, Any]
    ) -> IntakeResult:
        """Write to MemPalace (always) + Aleph (if keep) + emit triaged."""
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
                content=content, wing=self._wing, room=room,
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

        # Write extraction to Aleph (keep only)
        extraction_id = ""
        if verdict == "keep" and drawer_id:
            try:
                ext = self._aleph.write_extraction(
                    turn_id=drawer_id,
                    raw_event_id=event.id,
                    summary=summary,
                    entities=[e for e in entities if isinstance(e, dict)],
                    claims=[c for c in claims if isinstance(c, str)],
                    source=event.source,
                )
                extraction_id = ext.id
            except Exception as exc:
                _logger.error("itsme intake: Aleph write failed: %s", exc)

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
                "extraction_id": extraction_id,
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
            extraction_id=extraction_id,
        )


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
            {"verdict": "skip", "skip_reason": "llm_parse_error",
             "summary": "", "entities": [], "claims": []}
        ] * expected_count

    if not isinstance(data, list):
        _logger.warning("itsme intake: LLM returned non-array JSON, degrading")
        return [
            {"verdict": "skip", "skip_reason": "llm_parse_error",
             "summary": "", "entities": [], "claims": []}
        ] * expected_count

    # Normalize each entry
    result: list[dict[str, Any]] = []
    for item in data[:expected_count]:
        if not isinstance(item, dict):
            result.append({"verdict": "skip", "skip_reason": "malformed_entry",
                          "summary": "", "entities": [], "claims": []})
            continue
        result.append({
            "verdict": item.get("verdict", "skip"),
            "summary": item.get("summary", ""),
            "entities": item.get("entities", []),
            "claims": item.get("claims", []),
            "skip_reason": item.get("skip_reason", ""),
        })

    # Pad if LLM returned fewer than expected
    while len(result) < expected_count:
        result.append({"verdict": "skip", "skip_reason": "llm_truncated",
                      "summary": "", "entities": [], "claims": []})

    return result
