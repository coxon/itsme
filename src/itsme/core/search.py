"""Tri-leg search — wiki keyword + wiki embedding + MemPalace raw.

Three search legs over the knowledge store, merged and deduplicated:

    ┌──────────────┐   ┌─────────────────┐   ┌─────────────┐
    │  Aleph Wiki  │   │  Wiki Embedding │   │  MemPalace  │
    │  keyword     │   │  (MemPalace)    │   │   raw       │
    └──────┬───────┘   └────────┬────────┘   └──────┬──────┘
           │                    │                    │
           └──────────┬────────┴────────────────────┘
                      ▼
              merge + dedup
                      │
              ┌───────▼──────┐
              │ SearchHit[]  │
              └──────────────┘

Merge rules:

1. Wiki keyword hits first (consolidated knowledge — LLM-curated pages).
2. Wiki embedding hits (semantic match — pages that keyword missed).
3. MemPalace raw gap-fills (high recall — raw text for everything).

Embedding leg (T3.11+): wiki pages are synced to MemPalace in the
``aleph`` wing by IntakeProcessor. This allows ChromaDB embedding
search to find pages that simple keyword matching misses (e.g.,
"谁管产品" matching a page titled "海龙" with body "产品负责人").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from itsme.core.adapters import MemPalaceAdapter, MemPalaceHit
from itsme.core.adapters.naming import WIKI_WING
from itsme.core.aleph.wiki import Aleph, PageHit

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ helpers


def _page_hit_to_search_hit(hit: PageHit) -> SearchHit:
    """Convert an Aleph :class:`PageHit` to a unified :class:`SearchHit`."""
    slug = hit.meta.path.stem
    content_parts = [hit.meta.summary]
    if hit.snippet and hit.snippet != hit.meta.summary:
        content_parts.append(hit.snippet)
    content = "\n".join(p for p in content_parts if p)
    return SearchHit(
        kind="wiki",
        ref=f"wiki:{slug}",
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


# ------------------------------------------------------------------ types


@dataclass(frozen=True)
class SearchHit:
    """Unified search result from either engine.

    Attributes:
        kind: ``"wiki"`` for Aleph wiki hits, ``"verbatim"`` for MemPalace raw.
        ref: Provenance reference (``"wiki:<slug>"`` or
            ``"mempalace:<drawer_id>"``).
        content: The text: wiki summary/snippet or MemPalace raw.
        score: Normalized 0.0–1.0 score (higher = better).
        drawer_id: MemPalace drawer id (for dedup). Empty for wiki hits.
        metadata: Optional structured metadata (title, domain, etc.) for
            wiki hits.
    """

    kind: str
    ref: str
    content: str
    score: float
    drawer_id: str = ""
    metadata: dict[str, Any] | None = None


def dual_search(
    query: str,
    *,
    adapter: MemPalaceAdapter,
    aleph: Aleph | None = None,
    wing: str | None = None,
    limit: int = 5,
) -> list[SearchHit]:
    """Search Aleph wiki pages and MemPalace.

    Three search legs, merged in priority order:

    1. **Wiki keyword** — Aleph page search (title/alias/summary/body).
    2. **Wiki embedding** — MemPalace search in the ``aleph`` wing
       (pages synced for embedding by :class:`IntakeProcessor`).
       Catches semantic matches that keyword search misses.
    3. **MemPalace raw** — verbatim conversation turns (high recall).

    Wiki keyword and embedding hits are deduplicated by content
    overlap before merging with raw hits.

    Args:
        query: Natural-language search query.
        adapter: MemPalace backend for raw + embedding search.
        aleph: Aleph wiki adapter for keyword page search (None = skip).
        wing: Optional wing filter for MemPalace raw scope.
        limit: Max total results to return.

    Returns:
        Merged list of :class:`SearchHit`, wiki hits first,
        then MemPalace gap-fills, up to *limit* total.
    """
    if not query or not query.strip():
        return []

    # -- Leg 1: Wiki keyword search (consolidated knowledge, highest priority)
    wiki_hits: list[PageHit] = []
    if aleph is not None:
        try:
            wiki_hits = aleph.search(query, limit=limit)
        except Exception as exc:
            _logger.warning("itsme dual_search: wiki search failed: %s", exc)

    # -- Leg 2: Wiki embedding search (semantic match via MemPalace)
    wiki_embed_hits: list[MemPalaceHit] = []
    try:
        wiki_embed_hits = adapter.search(query, limit=limit, wing=WIKI_WING)
    except Exception as exc:
        _logger.warning("itsme dual_search: wiki embedding search failed: %s", exc)

    # -- Leg 3: MemPalace raw search (high recall)
    mp_hits: list[MemPalaceHit] = []
    try:
        mp_hits = adapter.search(query, limit=limit, wing=wing)
    except Exception as exc:
        _logger.warning("itsme dual_search: MemPalace search failed: %s", exc)

    # -- Merge
    results: list[SearchHit] = []

    # Wiki keyword hits first (consolidated knowledge)
    for hit in wiki_hits:
        results.append(_page_hit_to_search_hit(hit))

    # Wiki embedding hits (deduplicated against keyword hits)
    seen_content: set[str] = set()
    for r in results:
        # Use first 100 chars of content as dedup key
        seen_content.add(r.content[:100])

    for hit in wiki_embed_hits:
        # Skip if this content was already found by keyword search
        if hit.content[:100] in seen_content:
            continue
        seen_content.add(hit.content[:100])
        results.append(
            SearchHit(
                kind="wiki",
                ref=f"wiki:embed:{hit.drawer_id[:8]}",
                content=hit.content,
                score=hit.score,
            )
        )

    # MemPalace raw gap-fills
    seen_drawer_ids: set[str] = set()
    for hit in mp_hits:
        if hit.drawer_id in seen_drawer_ids:
            continue
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


def wiki_search(
    query: str,
    *,
    aleph: Aleph,
    limit: int = 5,
) -> list[SearchHit]:
    """Search only the Aleph wiki pages.

    Used by ``ask(mode='wiki')``.

    Args:
        query: Natural-language search query.
        aleph: Aleph wiki adapter.
        limit: Max results.

    Returns:
        List of :class:`SearchHit` with ``kind="wiki"``.
    """
    if not query or not query.strip():
        return []

    try:
        hits = aleph.search(query, limit=limit)
    except Exception as exc:
        _logger.warning("itsme wiki_search: search failed: %s", exc)
        return []

    results: list[SearchHit] = []
    for hit in hits:
        results.append(_page_hit_to_search_hit(hit))
    return results[:limit]
