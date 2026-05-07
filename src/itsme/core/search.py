"""Dual-engine search — vault wiki + MemPalace raw.

Parallel search over the Obsidian vault (consolidated wiki pages) and
MemPalace (raw verbatim memories). Results are merged and deduplicated.

Design (T3.0 — SQLite FTS5 index removed):

    ┌──────────────┐   ┌─────────────┐
    │  Vault Wiki  │   │  MemPalace  │
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

1. Vault wiki hits first (consolidated knowledge — LLM-curated pages).
2. MemPalace gap-fills (high recall — raw text for everything).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from itsme.core.adapters import MemPalaceAdapter, MemPalaceHit
from itsme.core.aleph.vault import AlephVault, PageHit

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    """Unified search result from either engine.

    Attributes:
        kind: ``"wiki"`` for vault hits, ``"verbatim"`` for MemPalace raw.
        ref: Provenance reference (``"vault:<slug>"`` or
            ``"mempalace:<drawer_id>"``).
        content: The text: vault summary/snippet or MemPalace raw.
        score: Normalized 0.0–1.0 score (higher = better).
        drawer_id: MemPalace drawer id (for dedup). Empty for vault hits.
        metadata: Optional structured metadata (title, domain, etc.) for
            vault hits.
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
    vault: AlephVault | None = None,
    wing: str | None = None,
    limit: int = 5,
) -> list[SearchHit]:
    """Search the Obsidian vault and MemPalace.

    If *vault* is provided, also searches wiki pages in the Obsidian
    vault. Wiki hits are ranked first (consolidated knowledge).

    Args:
        query: Natural-language search query.
        adapter: MemPalace backend for raw search.
        vault: AlephVault for wiki page search (None = skip).
        wing: Optional wing filter for MemPalace scope.
        limit: Max total results to return.

    Returns:
        Merged list of :class:`SearchHit`, vault wiki hits first,
        then MemPalace gap-fills, up to *limit* total.
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

    # -- MemPalace search (raw, high recall)
    mp_hits: list[MemPalaceHit] = []
    try:
        mp_hits = adapter.search(query, limit=limit, wing=wing)
    except Exception as exc:
        _logger.warning("itsme dual_search: MemPalace search failed: %s", exc)

    # -- Merge
    results: list[SearchHit] = []

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
