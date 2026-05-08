"""Curator — wiki maintenance worker.

Runs crosslink + refresh + dedup-pages after wiki round operations,
keeping the wiki self-consistent. Also callable standalone for manual
maintenance.

v0.0.4 scope:
- Post-round maintenance: refresh → crosslink → dedup-pages
- Standalone API for CLI / manual use
- Observability events (memory.curated with reason=crosslink / refresh / merge_candidate)

Future (v0.0.5+):
- Staleness detection (T4.3, rule-based + optional LLM)
- KG invalidation (T4.4)
- Wiki superseded_by marking (T4.5)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from itsme.core.adapters.mempalace import MemPalaceAdapter
from itsme.core.aleph.pipeline.crosslink import CrosslinkResult, crosslink
from itsme.core.aleph.pipeline.dedup_pages import DedupPagesResult, dedup_pages
from itsme.core.aleph.pipeline.refresh import RefreshResult, refresh
from itsme.core.aleph.wiki import Aleph
from itsme.core.events import EventBus, EventType

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ types


@dataclass
class CuratorResult:
    """Combined result of a curator pass."""

    crosslink: CrosslinkResult = field(default_factory=CrosslinkResult)
    refresh: RefreshResult = field(default_factory=RefreshResult)
    dedup: DedupPagesResult = field(default_factory=DedupPagesResult)

    @property
    def total_changes(self) -> int:
        return (
            self.crosslink.links_inserted
            + self.refresh.paragraphs_removed
            + self.refresh.history_dupes_removed
        )

    @property
    def merge_candidates(self) -> int:
        return self.dedup.count


# ------------------------------------------------------------------ curator


class Curator:
    """Wiki maintenance worker.

    Args:
        aleph: Aleph wiki adapter.
        bus: EventBus for emitting ``memory.curated`` events. Optional
            (None = no events emitted, useful for CLI / standalone use).
        adapter: MemPalace adapter for semantic duplicate detection.
            Optional (None = skip dedup-pages step).
    """

    def __init__(
        self,
        *,
        aleph: Aleph,
        bus: EventBus | None = None,
        adapter: MemPalaceAdapter | None = None,
    ) -> None:
        self._aleph = aleph
        self._bus = bus
        self._adapter = adapter

    def run(self, *, dry_run: bool = False) -> CuratorResult:
        """Run a full curator pass: refresh first, then crosslink.

        Refresh runs first because deduplicating paragraphs may remove
        text that would otherwise be crosslinked (wasted work).

        Args:
            dry_run: If True, compute changes but don't write files.

        Returns:
            :class:`CuratorResult` with combined stats.
        """
        result = CuratorResult()

        # Step 1: Refresh — dedup paragraphs + history entries
        try:
            result.refresh = refresh(self._aleph, dry_run=dry_run)
            if result.refresh.paragraphs_removed or result.refresh.history_dupes_removed:
                _logger.info(
                    "curator: refresh removed %d paragraphs, %d history dupes",
                    result.refresh.paragraphs_removed,
                    result.refresh.history_dupes_removed,
                )
                self._emit_curated(
                    reason="refresh",
                    details={
                        "paragraphs_removed": result.refresh.paragraphs_removed,
                        "history_dupes_removed": result.refresh.history_dupes_removed,
                        "pages_modified": result.refresh.pages_modified,
                        "details": result.refresh.details,
                    },
                    dry_run=dry_run,
                )
        except Exception as exc:
            _logger.error("curator: refresh failed: %s", exc)

        # Step 2: Crosslink — auto-insert [[wikilink]] backlinks
        try:
            result.crosslink = crosslink(self._aleph, dry_run=dry_run)
            if result.crosslink.links_inserted:
                _logger.info(
                    "curator: crosslink inserted %d links across %d pages",
                    result.crosslink.links_inserted,
                    result.crosslink.pages_modified,
                )
                self._emit_curated(
                    reason="crosslink",
                    details={
                        "links_inserted": result.crosslink.links_inserted,
                        "pages_modified": result.crosslink.pages_modified,
                        "details": result.crosslink.details,
                    },
                    dry_run=dry_run,
                )
        except Exception as exc:
            _logger.error("curator: crosslink failed: %s", exc)

        # Step 3: Dedup pages — detect semantically duplicate wiki pages
        if self._adapter is not None:
            try:
                result.dedup = dedup_pages(self._aleph, self._adapter)
                if result.dedup.count:
                    _logger.info(
                        "curator: found %d merge candidates",
                        result.dedup.count,
                    )
                    self._emit_curated(
                        reason="merge_candidate",
                        details={
                            "candidates": [
                                {
                                    "slug_a": c.slug_a,
                                    "title_a": c.title_a,
                                    "slug_b": c.slug_b,
                                    "title_b": c.title_b,
                                    "similarity": round(c.similarity, 3),
                                }
                                for c in result.dedup.candidates
                            ],
                            "count": result.dedup.count,
                        },
                        dry_run=dry_run,
                    )
            except Exception as exc:
                _logger.error("curator: dedup_pages failed: %s", exc)

        if result.total_changes or result.merge_candidates:
            _logger.info(
                "curator: total %d changes (crosslink: %d, refresh: %d para + %d hist)"
                ", %d merge candidates",
                result.total_changes,
                result.crosslink.links_inserted,
                result.refresh.paragraphs_removed,
                result.refresh.history_dupes_removed,
                result.merge_candidates,
            )
        else:
            _logger.debug("curator: no changes needed")

        return result

    def _emit_curated(
        self,
        *,
        reason: str,
        details: dict[str, object],
        dry_run: bool,
    ) -> None:
        """Emit a ``memory.curated`` event for observability."""
        if self._bus is None or dry_run:
            return
        self._bus.emit(
            type=EventType.MEMORY_CURATED,
            source="worker:curator",
            payload={
                "reason": reason,
                **details,
            },
        )
