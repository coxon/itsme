"""EventEnvelope and EventType — the contract every event must obey.

Locked decision: only **6 event types** (ARCHITECTURE §5). Adding a 7th
requires updating the architecture doc; `.coderabbit.yaml` flags this
file's path so PRs that widen the surface get scrutinized.

Payloads are intentionally `dict[str, Any]` in v0.0.1 — typed payload
models per event type land in v0.0.2+ as each producer/consumer is
implemented.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    """The 6 narrow event types (ARCHITECTURE §5).

    Adding a new member requires:
      1. Architecture doc update (§5).
      2. PR justification.
      3. Updates to all consumers that switch on type.
    """

    RAW_CAPTURED = "raw.captured"
    """Hook / explicit `remember()` ingested fresh content."""

    MEMORY_STORED = "memory.stored"
    """Adapter wrote to MemPalace (drawer created)."""

    MEMORY_ROUTED = "memory.routed"
    """Router decided where a `raw.captured` should land."""

    WIKI_PROMOTED = "wiki.promoted"
    """Promoter / `ask(promote=True)` published an Aleph wiki entry."""

    MEMORY_CURATED = "memory.curated"
    """Curator deduped, invalidated, or otherwise rewrote memory."""

    MEMORY_QUERIED = "memory.queried"
    """Reader served an `ask()` — for traffic analysis and skill tuning."""


class EventEnvelope(BaseModel):
    """Structural envelope every event ships in.

    Attributes:
        id: ULID (Crockford base32, exactly 26 chars). Monotonic — sort
            by id == sort by emission time.
        ts: UTC timestamp. Redundant with the timestamp embedded in the
            ULID, but kept verbatim for human readability and tooling.
        type: One of the 6 :class:`EventType` members.
        source: Free-form producer identifier (e.g. ``mcp.remember``,
            ``hook.cc.before-clear``, ``worker.router``). Useful for
            tracing, per-source rate limiting, and debugging.
        payload: Event-specific JSON-serializable dict. Untyped in
            v0.0.1; typed payload models join in v0.0.2+ as each
            producer/consumer is implemented.
        schema_version: Bump when the envelope shape itself changes.
            v0.0.1 == 1.

    The envelope is frozen — emit-then-don't-mutate is the rule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ULIDs are 26 chars of Crockford base32: digits 0-9 plus
    # uppercase A-Z minus the visually ambiguous I, L, O, U.
    # Reject anything that isn't shaped like a ULID — guards against
    # stray UUIDs, hex blobs, or arbitrary 26-char strings sneaking in.
    id: str = Field(pattern=r"^[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")
    ts: datetime
    type: EventType
    source: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1
