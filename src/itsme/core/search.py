"""Dual-engine search — wiki pages + MemPalace raw.

Parallel search over Aleph wiki pages (consolidated knowledge) and
MemPalace (raw verbatim memories). Results are merged and deduplicated.

Design (T3.0 — SQLite FTS5 index removed):

    ┌──────────────┐   ┌─────────────┐
    │  Aleph Wiki  │   │  MemPalace  │
    │  page search │   │   search    │
    └──────┬───────┘   └──────┬──────┘
           │                  │
           └────────┬─────────┘
                    ▼
            merge + dedup
                    │
            ┌───────▼──────┐
            │ SearchHit[]  │
            └──────────────┘

Merge rules:

1. Wiki hits first (consolidated knowledge — LLM-curated pages).
2. MemPalace gap-fills (high recall — raw text for everything).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from itsme.core.adapters import MemPalaceAdapter, MemPalaceHit
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

    If *aleph* is provided, also searches wiki pages in the Obsidian
    wiki. Wiki hits are ranked first (consolidated knowledge).

    Args:
        query: Natural-language search query.
        adapter: MemPalace backend for raw search.
        aleph: Aleph wiki adapter for page search (None = skip).
        wing: Optional wing filter for MemPalace scope.
        limit: Max total results to return.

    Returns:
        Merged list of :class:`SearchHit`, wiki hits first,
        then MemPalace gap-fills, up to *limit* total.
    """
    if not query or not query.strip():
        return []

    # -- Wiki search (consolidated knowledge, highest priority)
    wiki_hits: list[PageHit] = []
    if aleph is not None:
        try:
            wiki_hits = aleph.search(query, limit=limit)
        except Exception as exc:
            _logger.warning("itsme dual_search: wiki search failed: %s", exc)

    # -- MemPalace search (raw, high recall)
    mp_hits: list[MemPalaceHit] = []
    try:
        mp_hits = adapter.search(query, limit=limit, wing=wing)
    except Exception as exc:
        _logger.warning("itsme dual_search: MemPalace search failed: %s", exc)

    # -- Merge
    results: list[SearchHit] = []

    # Wiki hits first (consolidated knowledge)
    for hit in wiki_hits:
        results.append(_page_hit_to_search_hit(hit))

    # MemPalace gap-fills (high recall)
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
