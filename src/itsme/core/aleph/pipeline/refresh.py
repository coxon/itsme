"""Refresh — deduplicate paragraphs and clean redundancy in wiki pages.

When the same entity is discussed across multiple sessions, AlephRound's
``update_page(append_body=...)`` can produce duplicate or near-identical
paragraphs in a page's body.  This module does **deterministic**,
**LLM-free** cleanup:

1. **Exact-duplicate paragraph removal** — same text (after whitespace
   normalization) appearing more than once in a body.
2. **Duplicate History entry removal** — same ``- YYYY-MM-DD ...`` line
   repeated.
3. **Blank-line collapse** — 3+ consecutive blank lines → 2.

Near-duplicate (fuzzy) detection is deferred to the Curator (WS2).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from itsme.core.aleph.wiki import Aleph

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ types


@dataclass
class RefreshResult:
    """Result of a refresh pass."""

    pages_scanned: int = 0
    pages_modified: int = 0
    paragraphs_removed: int = 0
    history_dupes_removed: int = 0
    details: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers


def _normalize(text: str) -> str:
    """Normalize whitespace for comparison.

    Collapses runs of whitespace to single spaces and strips.
    """
    return re.sub(r"\s+", " ", text).strip()


def _dedup_paragraphs(body: str) -> tuple[str, int]:
    """Remove exact-duplicate paragraphs from *body*.

    Paragraphs are delimited by blank lines (``\\n\\n``).
    Comparison uses whitespace-normalized text.
    Protected blocks (fenced code, callouts) are never removed
    even if they repeat — they're typically templates.

    Returns (new_body, count_removed).
    """
    # Split on double-newline boundaries
    paragraphs = re.split(r"\n{2,}", body)

    seen: set[str] = set()
    kept: list[str] = []
    removed = 0

    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            continue

        # Never dedup code blocks or callout blocks — they're often
        # templates (dataviewjs) that legitimately repeat.
        is_protected = stripped.startswith("```") or stripped.startswith("> [!")

        normalized = _normalize(stripped)

        if not is_protected and normalized in seen:
            removed += 1
            _logger.debug("refresh: removed duplicate paragraph: %.60s…", stripped)
            continue

        seen.add(normalized)
        kept.append(para)

    if removed == 0:
        return body, 0

    new_body = "\n\n".join(kept)
    return new_body, removed


def _dedup_history(body: str) -> tuple[str, int]:
    """Remove duplicate History entries.

    History entries are lines starting with ``- `` inside the
    ``## History`` section.  Exact duplicates (after normalization)
    are removed, keeping the first occurrence.

    Returns (new_body, count_removed).
    """
    history_marker = "## History"
    idx = body.find(history_marker)
    if idx < 0:
        return body, 0

    before = body[:idx]
    history_section = body[idx:]

    lines = history_section.split("\n")
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0

    for line in lines:
        stripped = line.strip()
        # Only dedup list items (history entries)
        if stripped.startswith("- "):
            normalized = _normalize(stripped)
            if normalized in seen:
                removed += 1
                continue
            seen.add(normalized)
        kept.append(line)

    if removed == 0:
        return body, 0

    return before + "\n".join(kept), removed


def _collapse_blanks(body: str) -> str:
    """Collapse 3+ consecutive blank lines to 2."""
    return re.sub(r"\n{4,}", "\n\n\n", body)


# ------------------------------------------------------------------ public API


def refresh(aleph: Aleph, *, dry_run: bool = False) -> RefreshResult:
    """Scan all wiki pages and clean redundancy.

    Args:
        aleph: Aleph wiki adapter.
        dry_run: If True, compute changes but don't write files.

    Returns:
        :class:`RefreshResult` with counts and per-page details.
    """
    result = RefreshResult()
    pages = aleph.list_pages()
    result.pages_scanned = len(pages)

    for page in pages:
        meta, body = aleph.read_page(page.path)
        if meta is None or not body.strip():
            continue

        new_body = body
        page_para_removed = 0
        page_hist_removed = 0

        # Step 1: Dedup paragraphs
        new_body, para_removed = _dedup_paragraphs(new_body)
        page_para_removed += para_removed

        # Step 2: Dedup History entries
        new_body, hist_removed = _dedup_history(new_body)
        page_hist_removed += hist_removed

        # Step 3: Collapse blank lines
        new_body = _collapse_blanks(new_body)

        total_changes = page_para_removed + page_hist_removed
        if total_changes > 0:
            result.pages_modified += 1
            result.paragraphs_removed += page_para_removed
            result.history_dupes_removed += page_hist_removed

            detail_parts: list[str] = []
            if page_para_removed:
                detail_parts.append(f"{page_para_removed} para")
            if page_hist_removed:
                detail_parts.append(f"{page_hist_removed} hist")
            result.details.append(f"{page.path.stem}: -{', '.join(detail_parts)}")
            _logger.info(
                "refresh: %s — removed %d paragraphs, %d history dupes",
                page.path.stem,
                page_para_removed,
                page_hist_removed,
            )

            if not dry_run:
                full_path = aleph.root / page.path
                text = full_path.read_text(encoding="utf-8")
                old_body = aleph._extract_body(text)
                new_text = text.replace(old_body, new_body, 1)
                full_path.write_text(new_text, encoding="utf-8")

    _logger.info(
        "refresh: scanned %d pages, modified %d, " "removed %d paragraphs + %d history dupes",
        result.pages_scanned,
        result.pages_modified,
        result.paragraphs_removed,
        result.history_dupes_removed,
    )
    return result
