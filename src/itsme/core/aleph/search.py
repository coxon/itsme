"""Aleph search — T2.2.

v0.0.2: thin wrapper over ExtractionIndex.search(). Exists as a
separate module so v0.0.3 can extend it with embedding search and
wiki entry search without changing callers.
"""

from __future__ import annotations

from itsme.core.aleph.store.index import ExtractionHit, ExtractionIndex


def search_extractions(
    index: ExtractionIndex,
    query: str,
    *,
    limit: int = 5,
) -> list[ExtractionHit]:
    """Search the extraction index by keyword.

    Returns up to *limit* hits ordered by FTS5 rank (best first).
    """
    return index.search(query, limit=limit)
