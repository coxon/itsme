"""Dual-engine search — T2.19 + vault wiki search.

Parallel search over Aleph (structured, high precision), MemPalace
(raw, high recall), and optionally the Obsidian vault (wiki pages).
Results are merged and deduplicated.

Design (ARCHITECTURE §8.4):

    ┌────────────┐   ┌─────────────┐   ┌──────────────┐
    │   Aleph     │   │  MemPalace  │   │  Vault Wiki  │
    │ FTS5 index  │   │   search    │   │  page search │
    └─────┬──────┘   └──────┬──────┘   └──────┬───────┘
          │                 │                  │
          └────────┬────────┴──────────────────┘
                   ▼
           merge + dedup
                   │
           ┌───────▼──────┐
           │ SearchHit[]  │
           └──────────────┘

Merge rules:

1. Vault wiki hits first (consolidated knowledge — LLM-curated pages).
2. Aleph extraction hits next (high precision — per-turn summaries).
3. MemPalace gap-fills (high recall — raw text for turns Aleph
   didn't extract or whose entities didn't match the FTS query).
4. Dedup: if both engines hit the same ``drawer_id`` / ``turn_id``,
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
from itsme.core.aleph.vault import AlephVault, PageHit

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
    vault: AlephVault | None = None,
    wing: str | None = None,
    limit: int = 5,
) -> list[SearchHit]:
    """Search Aleph, MemPalace, and optionally the Obsidian vault.

    If *aleph* is None (LLM never wired / degraded), falls back to
    MemPalace-only (identical to ``mode='verbatim'`` behavior).

    If *vault* is provided, also searches wiki pages in the Obsidian
    vault. Wiki hits are ranked first (consolidated knowledge).

    Args:
        query: Natural-language search query.
        adapter: MemPalace backend for raw search.
        aleph: Aleph SDK for structured search (None = skip).
        vault: AlephVault for wiki page search (None = skip).
        wing: Optional wing filter for MemPalace scope.
        limit: Max total results to return.

    Returns:
        Merged list of :class:`SearchHit`, vault wiki hits first,
        then Aleph extractions, then MemPalace gap-fills, up to
        *limit* total.
    """
    if not query or not query.strip():
        return []

    # -- Vault wiki search (consolidated knowledge, highest priority)
    vault_hits: list[PageHit] = []
    if vault is not None:
        try:
            vault_hits = vault.search(query, limit=limit)
        except Exception as exc:
            _logger.warning("itsme dual_search: vault search failed: %s", exc)

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

    # Vault wiki hits first (consolidated knowledge)
    for hit in vault_hits:
        slug = hit.meta.path.stem
        content_parts = [hit.meta.summary]
        if hit.snippet and hit.snippet != hit.meta.summary:
            content_parts.append(hit.snippet)
        content = "\n".join(p for p in content_parts if p)

        results.append(
            SearchHit(
                kind="wiki",
                ref=f"vault:{slug}",
                content=content,
                score=hit.score,
                metadata={
                    "title": hit.meta.title,
                    "type": hit.meta.type,
                    "domain": hit.meta.domain,
                    "subcategory": hit.meta.subcategory,
                    "path": str(hit.meta.path),
                },
            )
        )

    # Aleph extraction hits next (high precision)
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


def vault_search(
    query: str,
    *,
    vault: AlephVault,
    limit: int = 5,
) -> list[SearchHit]:
    """Search only the Obsidian vault wiki pages.

    Used by ``ask(mode='wiki')``.

    Args:
        query: Natural-language search query.
        vault: AlephVault instance.
        limit: Max results.

    Returns:
        List of :class:`SearchHit` with ``kind="wiki"``.
    """
    if not query or not query.strip():
        return []

    try:
        hits = vault.search(query, limit=limit)
    except Exception as exc:
        _logger.warning("itsme vault_search: search failed: %s", exc)
        return []

    results: list[SearchHit] = []
    for hit in hits:
        slug = hit.meta.path.stem
        content_parts = [hit.meta.summary]
        if hit.snippet and hit.snippet != hit.meta.summary:
            content_parts.append(hit.snippet)
        content = "\n".join(p for p in content_parts if p)

        results.append(
            SearchHit(
                kind="wiki",
                ref=f"vault:{slug}",
                content=content,
                score=hit.score,
                metadata={
                    "title": hit.meta.title,
                    "type": hit.meta.type,
                    "domain": hit.meta.domain,
                    "subcategory": hit.meta.subcategory,
                    "path": str(hit.meta.path),
                },
            )
        )
    return results[:limit]
