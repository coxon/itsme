"""Aleph Round — consolidate MemPalace turns into vault wiki pages.

Takes filtered, meaningful conversation turns from MemPalace and
uses the LLM to decide which Aleph vault pages to create or update.
This is the "extraction → Aleph" step in the pipeline:

    对话 → intake filter → MemPalace → **Aleph round** → Obsidian vault

The round is NOT per-turn. It processes a batch of turns and makes
wiki-level decisions: entity resolution, page creation vs update,
cross-reference maintenance, index/log updates.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files as _files
from typing import Any

from itsme.core.aleph.vault import AlephVault, IndexEntry, PageMeta
from itsme.core.llm import LLMProvider, LLMUnavailableError

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ prompt

_ROUND_PROMPT: str | None = None


def _load_round_prompt() -> str:
    global _ROUND_PROMPT  # noqa: PLW0603
    if _ROUND_PROMPT is None:
        prompt_file = _files("itsme.core.aleph.prompts").joinpath("round.md")
        _ROUND_PROMPT = prompt_file.read_text(encoding="utf-8")
    return _ROUND_PROMPT


# ------------------------------------------------------------------ types


@dataclass
class RoundResult:
    """Result of one Aleph round."""

    pages_created: int = 0
    pages_updated: int = 0
    pages_skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TurnContent:
    """A single turn to be processed by the round."""

    role: str  # user | assistant
    content: str
    drawer_id: str = ""  # MemPalace drawer reference


# ------------------------------------------------------------------ round


class AlephRound:
    """Consolidate conversation turns into Aleph vault wiki pages.

    Args:
        vault: AlephVault adapter for reading/writing the Obsidian vault.
        llm: LLM provider for entity extraction and page decisions.
    """

    def __init__(self, *, vault: AlephVault, llm: LLMProvider) -> None:
        self._vault = vault
        self._llm = llm

    def process(self, turns: list[TurnContent]) -> RoundResult:
        """Process a batch of turns into vault page operations.

        Steps:
        1. Build context: existing pages list + turn content
        2. Ask LLM what pages to create/update
        3. Execute the page operations
        4. Update index.md and log.md

        Args:
            turns: Filtered, meaningful conversation turns.

        Returns:
            RoundResult with counts and any errors.
        """
        if not turns:
            return RoundResult()

        result = RoundResult()

        # Step 1: Get existing pages for entity resolution
        existing = self._vault.list_pages()
        existing_summary = self._format_existing_pages(existing)

        # Step 2: Format turns
        turn_text = self._format_turns(turns)

        # Step 3: Ask LLM
        try:
            operations = self._llm_extract(turn_text, existing_summary)
        except LLMUnavailableError as exc:
            _logger.warning("aleph round: LLM unavailable, skipping: %s", exc)
            result.errors.append(f"llm_unavailable: {exc}")
            return result
        except Exception as exc:
            _logger.error("aleph round: LLM failed: %s", exc)
            result.errors.append(f"llm_error: {exc}")
            return result

        if not operations:
            result.pages_skipped = len(turns)
            return result

        # Step 4: Execute operations
        today = date.today().isoformat()
        new_index_entries: list[IndexEntry] = []

        for op in operations:
            try:
                action = op.get("action", "")
                if action == "create":
                    self._execute_create(op, today)
                    result.pages_created += 1
                    idx_entry = self._make_index_entry_from_page(op["slug"], today)
                    if idx_entry is not None:
                        new_index_entries.append(idx_entry)
                elif action == "update":
                    self._execute_update(op, today)
                    result.pages_updated += 1
                    idx_entry = self._make_index_entry_from_page(op["slug"], today)
                    if idx_entry is not None:
                        new_index_entries.append(idx_entry)
                else:
                    _logger.warning("aleph round: unknown action %r, skipping", action)
                    result.pages_skipped += 1
            except Exception as exc:
                _logger.error("aleph round: operation failed: %s — %s", op, exc)
                result.errors.append(f"op_error: {exc}")

        # Step 5: Update index and log
        if new_index_entries:
            try:
                self._vault.update_index(new_index_entries)
            except Exception as exc:
                _logger.error("aleph round: index update failed: %s", exc)
                result.errors.append(f"index_error: {exc}")

        summary_parts: list[str] = []
        if result.pages_created:
            summary_parts.append(f"新增 {result.pages_created} 页")
        if result.pages_updated:
            summary_parts.append(f"更新 {result.pages_updated} 页")
        if summary_parts:
            try:
                self._vault.append_log(
                    action="INGEST",
                    source="itsme:aleph-round",
                    summary="，".join(summary_parts),
                )
            except Exception as exc:
                _logger.error("aleph round: log append failed: %s", exc)
                result.errors.append(f"log_error: {exc}")

        return result

    # ------------------------------------------------------- LLM interaction

    def _llm_extract(self, turn_text: str, existing_summary: str) -> list[dict[str, Any]]:
        """Call LLM to decide page operations."""
        user_message = (
            f"## Existing pages\n\n{existing_summary}\n\n" f"## Conversation turns\n\n{turn_text}"
        )

        raw = self._llm.complete(
            system=_load_round_prompt(),
            messages=[{"role": "user", "content": user_message}],
        )
        return _parse_round_response(raw)

    @staticmethod
    def _format_existing_pages(pages: list[PageMeta]) -> str:
        if not pages:
            return "(empty vault — no existing pages)"
        lines: list[str] = []
        for p in pages:
            lines.append(f"- `{p.path.stem}` ({p.type}, {p.domain}/{p.subcategory}): {p.summary}")
        return "\n".join(lines)

    @staticmethod
    def _format_turns(turns: list[TurnContent]) -> str:
        parts: list[str] = []
        for i, t in enumerate(turns):
            parts.append(f"Turn {i + 1} [{t.role}]:\n{t.content}")
        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------- page operations

    def _execute_create(self, op: dict[str, Any], today: str) -> None:
        """Create a new wiki page from an LLM operation."""
        slug = op["slug"]
        domain = op["domain"]
        subcategory = op["subcategory"]
        page_type = op["type"]
        title = op["title"]
        summary = op.get("summary", "")
        body_section = op.get("body_section", "")
        related = op.get("related", [])
        history_entry = op.get("history_entry", f"- {today} 创建，来源: itsme intake")

        frontmatter: dict[str, Any] = {
            "title": title,
            "type": page_type,
            "domain": domain,
            "subcategory": subcategory,
            "aliases": [],
            "summary": summary,
            "sources": [f"[[sources/itsme-{today}]]"],
            "links": [],
            "related": related,
            "tags": [f"wing/{domain}", f"type/{page_type}"],
            "last_verified": today,
        }

        body = f"# {title}\n\n"
        if body_section:
            body += f"> [!info] 核心摘要\n> {body_section}\n\n"
        body += f"## History\n{history_entry}\n"

        self._vault.write_page(
            slug=slug,
            domain=domain,
            subcategory=subcategory,
            frontmatter=frontmatter,
            body=body,
        )

    def _execute_update(self, op: dict[str, Any], today: str) -> None:
        """Update an existing wiki page from an LLM operation."""
        slug = op["slug"]
        meta = self._vault.find_page(slug)
        if meta is None:
            raise FileNotFoundError(f"Page not found for update: {slug}")

        fm_updates: dict[str, Any] = {"last_verified": today}
        if op.get("add_sources"):
            fm_updates["sources"] = op["add_sources"]
        if op.get("add_related"):
            fm_updates["related"] = op["add_related"]
        if op.get("summary"):
            fm_updates["summary"] = op["summary"]

        self._vault.update_page(
            meta.path,
            frontmatter_updates=fm_updates,
            append_body=op.get("append_body", ""),
            append_history=op.get("history_entry", f"- {today} 更新，来源: itsme intake"),
        )

    def _make_index_entry_from_page(self, slug: str, today: str) -> IndexEntry | None:
        """Build an IndexEntry from actual page metadata (not raw LLM op).

        Returns None if the page is not found (caller should skip upsert).
        """
        meta = self._vault.find_page(slug)
        if meta is None:
            _logger.warning("aleph round: page %r not found for index entry, skipping", slug)
            return None
        return IndexEntry(
            page_link=f"[[{slug}]]",
            type=meta.type,
            wing_sub=f"{meta.domain} / {meta.subcategory}",
            summary=meta.summary,
            date=today,
        )


# ------------------------------------------------------------------ parsing


def _parse_round_response(raw: str) -> list[dict[str, Any]]:
    """Parse LLM JSON array response, handling common quirks."""
    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        _logger.warning("aleph round: LLM returned non-JSON: %s", text[:200])
        return []

    if not isinstance(data, list):
        _logger.warning("aleph round: LLM returned non-array")
        return []

    # Validate each operation
    valid: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        action = item.get("action", "")

        required_create = ("slug", "domain", "subcategory", "type", "title")
        is_valid_create = action == "create" and all(
            isinstance(item.get(k), str) and bool(item[k].strip()) for k in required_create
        )
        is_valid_update = (
            action == "update" and isinstance(item.get("slug"), str) and bool(item["slug"].strip())
        )

        # Optional list fields must be lists if present
        list_keys = ("related", "add_sources", "add_related")
        lists_ok = all(isinstance(item[k], list) for k in list_keys if k in item)

        if (is_valid_create or is_valid_update) and lists_ok:
            valid.append(item)
        else:
            _logger.warning("aleph round: skipping malformed operation: %s", item)

    return valid
