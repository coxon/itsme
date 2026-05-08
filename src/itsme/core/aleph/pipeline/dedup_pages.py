"""Detect semantically duplicate wiki pages (T4.2).

Scans all wiki pages and uses MemPalace ``check_duplicate`` to find
page pairs whose content is semantically similar above a threshold.

**Does not auto-merge** — merging is dangerous (context loss, link
breakage). Instead reports ``MergeCandidate`` pairs for observability.
The curator emits ``memory.curated(reason="merge_candidate")`` so
operators can see duplicates in the status feed and decide manually.

Design:
- For each page, build a content string (title + summary + body prefix)
- Call ``check_duplicate(content, threshold)`` against MemPalace
- Parse matches to identify which OTHER page they belong to
- Filter self-matches (page finds its own MemPalace drawer)
- Deduplicate symmetric pairs (A↔B = B↔A)
- Return sorted by similarity descending
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from itsme.core.adapters.mempalace import MemPalaceAdapter
from itsme.core.aleph.wiki import Aleph, PageMeta

_logger = logging.getLogger(__name__)

#: Max chars of body to include in the duplicate check content.
#: Full body can be very long (dataviewjs etc.); the title + summary
#: + first ~300 chars of body is enough for semantic fingerprinting.
_BODY_PREFIX = 300

#: Default similarity threshold for merge candidates.
DEFAULT_THRESHOLD = 0.85


@dataclass
class MergeCandidate:
    """A pair of wiki pages that are semantically similar."""

    slug_a: str
    title_a: str
    slug_b: str
    title_b: str
    similarity: float


@dataclass
class DedupPagesResult:
    """Result of a duplicate page scan."""

    pages_scanned: int = 0
    candidates: list[MergeCandidate] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.candidates)


def _page_content(meta: PageMeta, body: str) -> str:
    """Build a content string for duplicate checking."""
    parts = [meta.title, meta.summary]
    if body:
        parts.append(body[:_BODY_PREFIX])
    return "\n".join(parts)


def _identify_page(
    match_content: str,
    slug_by_title: dict[str, str],
) -> str | None:
    """Try to identify which wiki page a MemPalace match belongs to.

    The MemPalace ``aleph`` wing stores pages as
    ``title\\nsummary\\nbody`` — the first line is the title.
    """
    first_line = match_content.split("\n")[0].strip()
    return slug_by_title.get(first_line)


def dedup_pages(
    aleph: Aleph,
    adapter: MemPalaceAdapter,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> DedupPagesResult:
    """Scan wiki pages for semantic duplicates.

    Args:
        aleph: Aleph wiki adapter.
        adapter: MemPalace adapter with ``check_duplicate`` support.
        threshold: Similarity threshold (0–1). Default 0.85.

    Returns:
        :class:`DedupPagesResult` with candidate pairs.
    """
    result = DedupPagesResult()
    pages = aleph.list_pages()
    result.pages_scanned = len(pages)

    if len(pages) < 2:
        return result

    # Build lookup: title → slug (for identifying matches)
    slug_by_title: dict[str, str] = {}
    page_data: list[tuple[PageMeta, str, str]] = []  # (meta, body, content)

    for page in pages:
        meta, body = aleph.read_page(page.path)
        if meta is None:
            continue
        slug = page.path.stem
        slug_by_title[meta.title] = slug
        content = _page_content(meta, body or "")
        page_data.append((meta, body or "", content))

    # Check each page against MemPalace
    pairs_seen: set[tuple[str, str]] = set()

    for meta, _body, content in page_data:
        slug = slug_by_title.get(meta.title)
        if slug is None:
            continue

        try:
            matches = adapter.check_duplicate(content, threshold=threshold)
        except Exception as exc:
            _logger.warning("dedup_pages: check_duplicate failed for %s: %s", slug, exc)
            continue

        for match in matches:
            # Identify which page this match belongs to
            match_slug = _identify_page(match.content, slug_by_title)
            if match_slug is None or match_slug == slug:
                continue  # self-match or unknown page

            # Deduplicate symmetric pairs
            pair = tuple(sorted([slug, match_slug]))
            if pair in pairs_seen:
                continue
            pairs_seen.add(pair)

            # Look up titles
            title_a = meta.title
            title_b = next(
                (m.title for m, _, _ in page_data if slug_by_title.get(m.title) == match_slug),
                match_slug,
            )

            result.candidates.append(
                MergeCandidate(
                    slug_a=pair[0],
                    title_a=title_a if pair[0] == slug else title_b,
                    slug_b=pair[1],
                    title_b=title_b if pair[1] == match_slug else title_a,
                    similarity=match.similarity,
                )
            )

    # Sort by similarity descending
    result.candidates.sort(key=lambda c: c.similarity, reverse=True)

    if result.candidates:
        _logger.info(
            "dedup_pages: found %d merge candidates across %d pages",
            len(result.candidates),
            result.pages_scanned,
        )
        for c in result.candidates:
            _logger.info(
                "  %.1f%%  %s ↔ %s",
                c.similarity * 100,
                c.slug_a,
                c.slug_b,
            )
    else:
        _logger.debug("dedup_pages: no duplicates found across %d pages", result.pages_scanned)

    return result
