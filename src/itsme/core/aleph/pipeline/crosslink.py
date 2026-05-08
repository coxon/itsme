"""Crosslink — auto-insert ``[[wikilink]]`` in wiki page bodies.

Scans all wiki pages, finds plain-text mentions of other pages'
titles or aliases, and replaces them with ``[[slug|text]]`` wikilinks.

Design rules:

- **First occurrence only** per target per page (standard wiki practice).
- **Never self-link** (a page doesn't link to its own title).
- **Skip protected zones**: frontmatter, code blocks (fenced ````` ```
  ````` and inline `` ` ``), existing ``[[...]]`` wikilinks, and
  ``dataviewjs`` blocks.
- **Longest match first** — "星图计划" is matched before "星图" to
  avoid partial replacements.
- **Case-insensitive** for Latin text; exact match for CJK.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from itsme.core.aleph.wiki import Aleph, PageMeta

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ types


@dataclass
class CrosslinkResult:
    """Result of a crosslink pass."""

    pages_scanned: int = 0
    pages_modified: int = 0
    links_inserted: int = 0
    details: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers

# Regions to protect from linking — we replace them with placeholders,
# do the linking, then restore them.

# Order matters: fenced code blocks first (greedy), then inline code,
# then existing wikilinks, then dataviewjs blocks.
_PROTECTED_RE = re.compile(
    r"```[\s\S]*?```"  # fenced code blocks
    r"|`[^`\n]+`"  # inline code
    r"|\[\[[^\]]*\]\]"  # existing [[wikilinks]]
    r"|> \[!.*?\].*?(?=\n[^>]|\n\n|\Z)",  # Obsidian callout blocks (single)
    re.MULTILINE,
)

_PLACEHOLDER = "\x00PROTECTED_{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00PROTECTED_(\d+)\x00")


def _shield_protected(body: str) -> tuple[str, list[str]]:
    """Replace protected regions with numbered placeholders.

    Returns (shielded_body, list_of_originals).
    """
    originals: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        idx = len(originals)
        originals.append(m.group(0))
        return _PLACEHOLDER.format(idx)

    shielded = _PROTECTED_RE.sub(_replace, body)
    return shielded, originals


def _unshield(body: str, originals: list[str]) -> str:
    """Restore placeholders back to their original text."""

    def _restore(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return originals[idx]

    return _PLACEHOLDER_RE.sub(_restore, body)


def _build_target_map(pages: list[PageMeta]) -> list[tuple[str, str, str]]:
    """Build (match_text, slug, display_text) tuples, longest first.

    Each entry means: "if you see *match_text* in a body, replace with
    ``[[slug|display_text]]``".

    Sorted longest-first so "星图计划" is tried before "星图".
    """
    entries: list[tuple[str, str, str]] = []
    for page in pages:
        # Title → slug
        if page.title.strip():
            entries.append((page.title.strip(), page.path.stem, page.title.strip()))
        # Aliases → slug (display as alias text)
        for alias in page.aliases:
            alias = alias.strip()
            if alias:
                entries.append((alias, page.path.stem, alias))

    # Deduplicate by match_text (keep first = title wins over alias)
    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for match_text, slug, display in entries:
        key = match_text.lower()
        if key not in seen:
            seen.add(key)
            unique.append((match_text, slug, display))

    # Sort longest first
    unique.sort(key=lambda e: len(e[0]), reverse=True)
    return unique


def _make_pattern(match_text: str) -> re.Pattern[str]:
    """Build a regex pattern for *match_text*.

    - Latin words: word-boundary delimited, case-insensitive.
    - CJK text: no word boundaries needed (CJK doesn't have spaces).
    - Mixed: match as-is.
    """
    # Check if text is purely ASCII/Latin
    is_latin = all(ord(c) < 0x2E80 for c in match_text if not c.isspace())

    if is_latin:
        # Word-boundary delimited, case-insensitive
        return re.compile(
            r"\b" + re.escape(match_text) + r"\b",
            re.IGNORECASE,
        )
    else:
        # CJK or mixed — match as-is (no word boundaries for CJK)
        return re.compile(re.escape(match_text))


def _crosslink_body(
    body: str,
    *,
    targets: list[tuple[str, str, str]],
    self_slug: str,
) -> tuple[str, int]:
    """Insert wikilinks into *body* for matching targets.

    Returns (new_body, count_of_links_inserted).
    """
    if not body.strip() or not targets:
        return body, 0

    # Shield protected regions
    shielded, originals = _shield_protected(body)

    # Pre-scan: which slugs already have [[slug...]] links in the body?
    # If a page already links to a target (e.g. [[xai|xAI]]), we skip
    # ALL remaining plain-text occurrences of that target — the page
    # already references it, adding more links is noise.
    already_linked: set[str] = set()
    for orig in originals:
        if orig.startswith("[["):
            # Extract slug from [[slug]] or [[slug|display]]
            inner = orig[2:].rstrip("]")
            link_slug = inner.split("|")[0]
            already_linked.add(link_slug)

    count = 0
    linked_slugs: set[str] = set()

    for match_text, slug, display in targets:
        # Never self-link
        if slug == self_slug:
            continue
        # Skip if page already links to this target
        if slug in already_linked:
            continue
        # Only first occurrence per target
        if slug in linked_slugs:
            continue

        pattern = _make_pattern(match_text)
        m = pattern.search(shielded)
        if m:
            # Build replacement — use [[slug|display]] if display != slug,
            # otherwise just [[slug]]
            matched_actual = m.group(0)
            if slug in (display, matched_actual):
                replacement = f"[[{slug}]]"
            else:
                replacement = f"[[{slug}|{matched_actual}]]"

            # Shield the replacement so subsequent patterns don't match
            # text inside wikilinks we just inserted (e.g. "星图计划"
            # should not leave "星图" matchable inside [[starmap-plan|星图计划]]).
            idx = len(originals)
            originals.append(replacement)
            placeholder = _PLACEHOLDER.format(idx)

            # Replace only the first occurrence
            shielded = shielded[: m.start()] + placeholder + shielded[m.end() :]
            linked_slugs.add(slug)
            count += 1

    # Unshield
    result = _unshield(shielded, originals)
    return result, count


# ------------------------------------------------------------------ public API


def crosslink(aleph: Aleph, *, dry_run: bool = False) -> CrosslinkResult:
    """Scan all wiki pages and auto-insert ``[[wikilink]]`` backlinks.

    Args:
        aleph: Aleph wiki adapter.
        dry_run: If True, compute changes but don't write files.

    Returns:
        :class:`CrosslinkResult` with counts and per-page details.
    """
    result = CrosslinkResult()
    pages = aleph.list_pages()
    result.pages_scanned = len(pages)

    if not pages:
        return result

    # Build target map from all pages
    targets = _build_target_map(pages)

    if not targets:
        return result

    for page in pages:
        meta, body = aleph.read_page(page.path)
        if meta is None or not body.strip():
            continue

        new_body, count = _crosslink_body(
            body,
            targets=targets,
            self_slug=page.path.stem,
        )

        if count > 0:
            result.pages_modified += 1
            result.links_inserted += count
            result.details.append(f"{page.path.stem}: +{count} links")
            _logger.info(
                "crosslink: %s — inserted %d wikilinks",
                page.path.stem,
                count,
            )

            if not dry_run:
                full_path = aleph.root / page.path
                # Re-read the full file to preserve frontmatter exactly
                text = full_path.read_text(encoding="utf-8")
                old_body = aleph._extract_body(text)
                # Replace old body with new body
                new_text = text.replace(old_body, new_body, 1)
                full_path.write_text(new_text, encoding="utf-8")

    _logger.info(
        "crosslink: scanned %d pages, modified %d, inserted %d links",
        result.pages_scanned,
        result.pages_modified,
        result.links_inserted,
    )
    return result
