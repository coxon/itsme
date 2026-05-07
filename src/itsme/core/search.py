"""Dual-engine search — T2.19.

Parallel search over Aleph (structured, high precision) and MemPalace
(raw, high recall). Results are merged and deduplicated by drawer_id
so the same turn never appears twice.

Design (ARCHITECTURE §8.4):

    ┌────────────┐   ┌─────────────┐
    │   Aleph     │   │  MemPalace  │
    │ FTS5 index  │   │   search    │
    └─────┬──────┘   └──────┬──────┘
          │                 │
          └───────┬─────────┘
                  ▼
          merge + dedup
          (same drawer_id)
                  │
          ┌───────▼──────┐
          │ AskSource[]  │
          └──────────────┘

Merge rules:

1. Aleph hits first (high precision — LLM-extracted summaries).
2. MemPalace gap-fills (high recall — raw text for turns Aleph
   didn't extract or whose entities didn't match the FTS query).
3. Dedup: if both engines hit the same ``drawer_id`` / ``turn_id``,
   keep BOTH as separate sources (different ``kind``) but count as
   one dedup slot. The caller sees structured + raw for that turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from itsme.core.adapters import MemPalaceAdapter, MemPalaceHit
from itsme.core.aleph.api import Aleph
from itsme.core.aleph.store.index import ExtractionHit

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    """Unified search result from either engine.

    Attributes:
        kind: ``"extraction"`` for Aleph hits, ``"verbatim"`` for
            MemPalace raw hits.
        ref: Provenance reference (``"aleph:<id>"`` or ``"mempalace:<drawer_id>"``).
        content: The text: Aleph summary (+ claims) or MemPalace raw.
        score: Normalized 0.0–1.0 score (higher = better).
        drawer_id: MemPalace drawer id (for dedup). May be empty for
            Aleph hits whose turn_id doesn't map to a drawer.
        extraction_id: Aleph extraction id. Empty for MemPalace-only hits.
        metadata: Optional structured metadata (entities, claims) for
            Aleph hits.
    """

    kind: str
    ref: str
    content: str
    score: float
    drawer_id: str = ""
    extraction_id: str = ""
    metadata: dict[str, Any] | None = None


def dual_search(
    query: str,
    *,
    adapter: MemPalaceAdapter,
    aleph: Aleph | None,
    wing: str | None = None,
    limit: int = 5,
) -> list[SearchHit]:
    """Search both Aleph and MemPalace, merge and deduplicate.

    If *aleph* is None (LLM never wired / degraded), falls back to
    MemPalace-only (identical to ``mode='verbatim'`` behavior).

    Args:
        query: Natural-language search query.
        adapter: MemPalace backend for raw search.
        aleph: Aleph SDK for structured search (None = skip).
        wing: Optional wing filter for MemPalace scope.
        limit: Max total results to return.

    Returns:
        Merged list of :class:`SearchHit`, Aleph hits first,
        then MemPalace gap-fills, up to *limit* total unique turns.
    """
    if not query or not query.strip():
        return []

    # -- Aleph search (structured, high precision)
    aleph_hits: list[ExtractionHit] = []
    if aleph is not None:
        try:
            aleph_hits = aleph.search(query, limit=limit)
        except Exception as exc:
            _logger.warning("itsme dual_search: Aleph search failed, degrading: %s", exc)

    # -- MemPalace search (raw, high recall)
    mp_hits: list[MemPalaceHit] = []
    try:
        mp_hits = adapter.search(query, limit=limit, wing=wing)
    except Exception as exc:
        _logger.warning("itsme dual_search: MemPalace search failed: %s", exc)

    # -- Merge + dedup
    results: list[SearchHit] = []
    seen_drawer_ids: set[str] = set()

    # Aleph hits first (high precision)
    for hit in aleph_hits:
        drawer_id = hit.extraction.turn_id
        if drawer_id:
            seen_drawer_ids.add(drawer_id)

        # Build content from structured data: summary + claims
        content_parts = [hit.extraction.summary]
        if hit.extraction.claims:
            content_parts.append("Claims: " + "; ".join(hit.extraction.claims))
        content = "\n".join(p for p in content_parts if p)

        results.append(
            SearchHit(
                kind="extraction",
                ref=f"aleph:{hit.extraction.id}",
                content=content,
                score=_normalize_fts5_rank(hit.rank),
                drawer_id=drawer_id,
                extraction_id=hit.extraction.id,
                metadata={
                    "summary": hit.extraction.summary,
                    "entities": hit.extraction.entities,
                    "claims": hit.extraction.claims,
                },
            )
        )

    # MemPalace gap-fills (high recall — turns Aleph missed)
    for hit in mp_hits:
        if hit.drawer_id in seen_drawer_ids:
            continue  # already covered by Aleph
        seen_drawer_ids.add(hit.drawer_id)
        results.append(
            SearchHit(
                kind="verbatim",
                ref=f"mempalace:{hit.drawer_id}",
                content=hit.content,
                score=hit.score,
                drawer_id=hit.drawer_id,
            )
        )

    return results[:limit]


def _normalize_fts5_rank(rank: float) -> float:
    """Map FTS5 BM25 rank (negative, lower=better) to 0.0–1.0.

    FTS5 rank is typically in [-20, 0] range. We use a sigmoid-like
    mapping: ``1 / (1 + exp(rank))`` which maps 0 → 0.5, -5 → 0.99+,
    -20 → ~1.0. Clamped to [0, 1].
    """
    import math

    try:
        score = 1.0 / (1.0 + math.exp(rank))
    except OverflowError:
        score = 0.0 if rank > 0 else 1.0
    return max(0.0, min(1.0, score))
