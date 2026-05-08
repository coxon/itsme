"""Aleph wiki — read/write adapter for the Obsidian knowledge wiki.

Aleph is a Karpathy-style LLM Wiki stored as plain markdown files in
an Obsidian wiki. This adapter handles the mechanical parts:

- Read wiki structure (dna.md wings, index.md catalog)
- Search existing pages (frontmatter + full text)
- Write/update pages following dna.md conventions
- Maintain index.md and log.md

It does NOT handle LLM-powered decisions (which entities to extract,
whether to create vs update). That's the job of the intake/round
pipeline that sits above this adapter.

Layout::

    ~/Documents/Aleph/
    ├── CLAUDE.md
    ├── dna.md
    ├── index.md
    ├── log.md
    ├── sources/
    └── wings/
        ├── technology/{ai,engineering,products,people}/
        ├── life/{health,travel,home,people}/
        ├── financial/{markets,crypto,companies,personal}/
        ├── gossip/{ideas,takes,media,quotes}/
        └── work/{meetings,projects,people}/
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------- CJK tokenization

# CJK Unified Ideographs + Extension A + Hiragana + Katakana + Hangul.
# Same range as ``adapters/mempalace.py`` — kept in sync.
_CJK_RE = re.compile(
    r"[぀-ゟ"  # Hiragana
    r"゠-ヿ"  # Katakana
    r"㐀-䶿"  # CJK Extension A
    r"一-鿿"  # CJK Unified Ideographs
    r"가-힯"  # Hangul Syllables
    r"]"
)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _search_tokens(text: str) -> set[str]:
    """Split *text* into search tokens with CJK-aware tokenization.

    Latin / numeric runs keep whole-word boundaries (lowercased).
    CJK runs are split into individual characters so that a query like
    "海龙负责什么" can match a page titled "海龙" via per-character
    substring matching.

    This mirrors the fix applied to ``InMemoryMemPalaceAdapter`` in
    T1.13.5 — without per-character CJK tokenization, ``split()``
    produces a single giant token that fails substring matching against
    shorter content.
    """
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        cjk_chars = _CJK_RE.findall(raw)
        if cjk_chars and len(cjk_chars) == len(raw):
            # Pure CJK run: individual characters
            out.update(cjk_chars)
        elif cjk_chars:
            # Mixed run (e.g. "v2版本"): whole token + individual CJK chars
            out.add(raw.lower())
            out.update(cjk_chars)
        else:
            # Pure Latin/numeric: whole token lowercased
            out.add(raw.lower())
    return out


# ------------------------------------------------------------------ types


@dataclass(frozen=True)
class PageMeta:
    """Parsed frontmatter of an Aleph wiki page."""

    path: Path  # relative to Aleph root, e.g. wings/technology/ai/rag.md
    title: str
    type: str  # concept | person | project | decision
    domain: str  # technology | life | financial | gossip | work
    subcategory: str
    summary: str
    aliases: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    last_verified: str = ""
    # extra fields for decisions
    date: str = ""
    status: str = ""


@dataclass(frozen=True)
class PageHit:
    """A search result from the wiki."""

    meta: PageMeta
    score: float  # 0.0 – 1.0
    snippet: str  # matched text excerpt


@dataclass(frozen=True)
class IndexEntry:
    """One row in index.md."""

    page_link: str  # wikilink, e.g. [[llm-wiki-pattern]]
    type: str
    wing_sub: str  # e.g. "technology / ai"
    summary: str
    date: str


# ------------------------------------------------------------------ Aleph


class Aleph:
    """Read/write adapter for the Obsidian Aleph knowledge wiki.

    Args:
        root: Absolute path to the Aleph root directory
            (the directory containing ``dna.md``).
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        if not (self._root / "dna.md").exists():
            raise FileNotFoundError(f"Not an Aleph wiki (dna.md missing): {self._root}")
        self._wings_dir = self._root / "wings"
        self._sources_dir = self._root / "sources"

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------- path safety

    def _safe_resolve(self, path: Path) -> Path:
        """Resolve *path* and verify it stays within the Aleph root.

        Raises ValueError if the resolved path escapes the wiki
        (e.g. via ``../`` segments or symlinks).
        """
        resolved = path.resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise ValueError(
                f"Path escapes Aleph root: {path} resolves to {resolved} " f"(root: {self._root})"
            )
        return resolved

    # ------------------------------------------------------- read operations

    def list_pages(self) -> list[PageMeta]:
        """List all wiki pages under wings/."""
        pages: list[PageMeta] = []
        for md_file in sorted(self._wings_dir.rglob("*.md")):
            meta = self._parse_frontmatter(md_file)
            if meta is not None:
                pages.append(meta)
        return pages

    def read_page(self, rel_path: str | Path) -> tuple[PageMeta | None, str]:
        """Read a page by relative path. Returns (meta, body)."""
        full = self._safe_resolve(self._root / rel_path)
        if not full.exists():
            return None, ""
        text = full.read_text(encoding="utf-8")
        meta = self._parse_frontmatter(full)
        body = self.extract_body(text)
        return meta, body

    def find_page(self, slug: str) -> PageMeta | None:
        """Find a page by slug (filename without .md).

        Searches all wings. Returns the first match or None.
        Raises ValueError if multiple pages share the same slug.
        """
        matches: list[PageMeta] = []
        for md_file in self._wings_dir.rglob(f"{slug}.md"):
            meta = self._parse_frontmatter(md_file)
            if meta is not None:
                matches.append(meta)
        if len(matches) > 1:
            paths = [str(m.path) for m in matches]
            raise ValueError(f"Duplicate slug '{slug}' found in: {paths}")
        return matches[0] if matches else None

    def find_by_title_or_alias(self, name: str) -> PageMeta | None:
        """Find a page whose title or alias matches *name* (case-insensitive)."""
        name_lower = name.lower().strip()
        for page in self.list_pages():
            if page.title.lower() == name_lower:
                return page
            if any(a.lower() == name_lower for a in page.aliases):
                return page
        return None

    def read_index(self) -> list[IndexEntry]:
        """Parse index.md into structured entries."""
        index_path = self._root / "index.md"
        if not index_path.exists():
            return []
        text = index_path.read_text(encoding="utf-8")
        entries: list[IndexEntry] = []
        # Parse table rows: | [[link]] | type | wing / sub | summary | date |
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or line.startswith("| 页面") or line.startswith("|--"):
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                entries.append(
                    IndexEntry(
                        page_link=cols[0],
                        type=cols[1],
                        wing_sub=cols[2],
                        summary=cols[3],
                        date=cols[4],
                    )
                )
        return entries

    # ------------------------------------------------------ search

    def search(self, query: str, *, limit: int = 5) -> list[PageHit]:
        """Simple keyword search across page titles, aliases, summaries, and bodies.

        Scoring: title/alias match > summary match > body match.
        Not a replacement for proper FTS — good enough for entity
        resolution and page lookup during ingest.
        """
        if not query or not query.strip():
            return []

        q_terms = _search_tokens(query)
        if not q_terms:
            return []

        hits: list[tuple[float, PageMeta, str]] = []

        for md_file in self._wings_dir.rglob("*.md"):
            meta = self._parse_frontmatter(md_file)
            if meta is None:
                continue

            text = md_file.read_text(encoding="utf-8")
            body = self.extract_body(text).lower()
            score = 0.0
            snippet = ""

            # Title match (highest weight)
            title_lower = meta.title.lower()
            title_hits = sum(1 for t in q_terms if t in title_lower)
            if title_hits:
                score += 0.5 * (title_hits / len(q_terms))
                snippet = meta.title

            # Alias match
            for alias in meta.aliases:
                alias_lower = alias.lower()
                alias_hits = sum(1 for t in q_terms if t in alias_lower)
                if alias_hits:
                    score += 0.3 * (alias_hits / len(q_terms))
                    if not snippet:
                        snippet = alias

            # Summary match
            summary_lower = meta.summary.lower()
            summary_hits = sum(1 for t in q_terms if t in summary_lower)
            if summary_hits:
                score += 0.2 * (summary_hits / len(q_terms))
                if not snippet:
                    snippet = meta.summary

            # Body match (lowest weight, but catches everything)
            body_hits = sum(1 for t in q_terms if t in body)
            if body_hits:
                score += 0.1 * (body_hits / len(q_terms))
                if not snippet:
                    # Extract a snippet around first match
                    for t in q_terms:
                        idx = body.find(t)
                        if idx >= 0:
                            start = max(0, idx - 50)
                            end = min(len(body), idx + 100)
                            snippet = body[start:end].strip()
                            break

            if score > 0:
                hits.append((min(score, 1.0), meta, snippet))

        hits.sort(key=lambda h: h[0], reverse=True)
        return [PageHit(meta=m, score=s, snippet=sn) for s, m, sn in hits[:limit]]

    # ------------------------------------------------------ write operations

    def write_page(
        self,
        *,
        slug: str,
        domain: str,
        subcategory: str,
        frontmatter: dict[str, Any],
        body: str,
    ) -> Path:
        """Create a new wiki page. Returns the path relative to the wiki root.

        Creates the subcategory directory if it doesn't exist.
        Raises FileExistsError if the page already exists (use update_page).
        Raises ValueError if the slug already exists in another wing,
            or if any path component contains traversal sequences.
        """
        # Validate path components — reject traversal before any I/O
        for component in (slug, domain, subcategory):
            if ".." in component or "/" in component or "\\" in component:
                raise ValueError(f"Path component contains traversal chars: {component!r}")

        # Check global slug uniqueness
        existing = self.find_page(slug)
        if existing is not None:
            raise FileExistsError(f"Slug '{slug}' already exists at {existing.path}")

        page_dir = self._wings_dir / domain / subcategory
        page_dir.mkdir(parents=True, exist_ok=True)
        page_path = page_dir / f"{slug}.md"
        self._safe_resolve(page_path)  # belt-and-suspenders containment check

        content = self._render_page(frontmatter, body)
        page_path.write_text(content, encoding="utf-8")
        _logger.info("aleph: created page %s", page_path.relative_to(self._root))
        return page_path.relative_to(self._root)

    def update_page(
        self,
        rel_path: str | Path,
        *,
        frontmatter_updates: dict[str, Any] | None = None,
        append_body: str = "",
        append_history: str = "",
    ) -> None:
        """Update an existing page's frontmatter and/or body.

        - frontmatter_updates: merged into existing frontmatter (lists are extended, not replaced)
        - append_body: appended before the History section
        - append_history: appended to the History section
        """
        full = self._safe_resolve(self._root / rel_path)
        if not full.exists():
            raise FileNotFoundError(f"Page not found: {full}")

        text = full.read_text(encoding="utf-8")
        fm, body = self._split_frontmatter_and_body(text)

        # Merge frontmatter updates
        if frontmatter_updates:
            for key, value in frontmatter_updates.items():
                if key in fm and isinstance(fm[key], list) and isinstance(value, list):
                    # Extend lists, dedup
                    existing = set(str(x) for x in fm[key])
                    for v in value:
                        if str(v) not in existing:
                            fm[key].append(v)
                            existing.add(str(v))
                else:
                    fm[key] = value

        # Append to body (before History section)
        if append_body:
            history_marker = "\n## History"
            if history_marker in body:
                idx = body.index(history_marker)
                body = body[:idx] + "\n" + append_body + "\n" + body[idx:]
            else:
                body = body + "\n" + append_body

        # Append to history
        if append_history:
            if "## History" in body:
                body = body.rstrip() + "\n" + append_history + "\n"
            else:
                body = body.rstrip() + "\n\n## History\n" + append_history + "\n"

        content = self._render_page(fm, body)
        full.write_text(content, encoding="utf-8")
        _logger.info("aleph: updated page %s", rel_path)

    def update_index(self, new_entries: list[IndexEntry]) -> None:
        """Add or update entries in index.md."""
        index_path = self._root / "index.md"
        existing = self.read_index()

        # Build lookup by page_link
        by_link: dict[str, IndexEntry] = {e.page_link: e for e in existing}
        for entry in new_entries:
            by_link[entry.page_link] = entry

        # Rebuild index.md
        lines = [
            "# Aleph Index\n",
            "<!-- Claude 维护，记录所有 wiki 页面。请勿手动大幅修改。 -->\n",
            "| 页面 | 类型 | Wing / 子类 | 摘要 | 更新日期 |",
            "|------|------|------------|------|---------|",
        ]
        for entry in sorted(by_link.values(), key=lambda e: e.page_link):
            # Sanitize cells: collapse newlines, escape pipe chars
            link = _sanitize_cell(entry.page_link)
            typ = _sanitize_cell(entry.type)
            ws = _sanitize_cell(entry.wing_sub)
            summ = _sanitize_cell(entry.summary)
            dt = _sanitize_cell(entry.date)
            lines.append(f"| {link} | {typ} | {ws} | {summ} | {dt} |")

        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def append_log(
        self,
        *,
        action: str,
        source: str,
        summary: str,
    ) -> None:
        """Append an entry to log.md.

        Args:
            action: INGEST | QUERY | LINT | UPDATE
            source: source identifier
            summary: human-readable summary of what happened
        """
        log_path = self._root / "log.md"
        today = date.today().isoformat()
        entry = f"[{action}] {today} | {source} | {summary}\n"

        if log_path.exists():
            text = log_path.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                text += "\n"
            text += entry
        else:
            text = "# Aleph Log\n\n<!-- append-only，不要修改已有行 -->\n\n" + entry

        log_path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------ internals

    def _parse_frontmatter(self, md_file: Path) -> PageMeta | None:
        """Parse YAML frontmatter from a markdown file."""
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            return None

        fm, _ = self._split_frontmatter_and_body(text)
        if not fm:
            return None

        rel_path = md_file.relative_to(self._root)

        return PageMeta(
            path=rel_path,
            title=fm.get("title", md_file.stem),
            type=fm.get("type", ""),
            domain=fm.get("domain", ""),
            subcategory=fm.get("subcategory", ""),
            summary=fm.get("summary", ""),
            aliases=fm.get("aliases", []) or [],
            sources=fm.get("sources", []) or [],
            related=fm.get("related", []) or [],
            tags=fm.get("tags", []) or [],
            last_verified=str(fm.get("last_verified", "")),
            date=str(fm.get("date", "")),
            status=fm.get("status", ""),
        )

    @staticmethod
    def _split_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
        """Split a markdown file into (frontmatter_dict, body_string)."""
        if not text.startswith("---"):
            return {}, text

        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text

        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return {}, text

        return fm, parts[2].lstrip("\n")

    @staticmethod
    def extract_body(text: str) -> str:
        """Strip frontmatter, return body only."""
        if not text.startswith("---"):
            return text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return text
        return parts[2].lstrip("\n")

    @staticmethod
    def _render_page(frontmatter: dict[str, Any], body: str) -> str:
        """Render frontmatter + body into a complete markdown page."""
        fm_str = yaml.dump(
            frontmatter,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ).rstrip("\n")
        return f"---\n{fm_str}\n---\n\n{body}"


def _sanitize_cell(value: str) -> str:
    """Collapse newlines and escape pipes in a markdown table cell."""
    return value.replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()


# Backward-compat alias (will be removed in v0.1.0)
AlephVault = Aleph
