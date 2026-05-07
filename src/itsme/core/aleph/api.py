"""Aleph internal SDK — T2.3.

The rest of itsme talks to Aleph through this module. In v0.0.2 it
wraps the extraction index; v0.0.3 will add wiki entry operations.

Usage::

    aleph = Aleph(db_path="~/.itsme/aleph.db")
    aleph.write_extraction(
        turn_id="drawer_001",
        raw_event_id="evt_001",
        summary="User chose Postgres",
        entities=[{"name": "Postgres", "type": "database"}],
        claims=["Postgres chosen for concurrent writes"],
    )
    hits = aleph.search("Postgres", limit=5)
    aleph.close()
"""

from __future__ import annotations

import os
from pathlib import Path

from itsme.core.aleph.store.index import Extraction, ExtractionHit, ExtractionIndex


def _resolve_aleph_db_path() -> Path:
    """Default Aleph DB path: ``$ITSME_ALEPH_DB`` or ``~/.itsme/aleph.db``."""
    raw = os.environ.get("ITSME_ALEPH_DB")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".itsme" / "aleph.db"


class Aleph:
    """Aleph internal SDK — the single entry point for extraction operations.

    Args:
        db_path: Path to the sqlite database. Use ``":memory:"`` for
            tests. Defaults to :func:`_resolve_aleph_db_path`.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        resolved = db_path if db_path is not None else _resolve_aleph_db_path()
        self._index = ExtractionIndex(resolved)

    @property
    def index(self) -> ExtractionIndex:
        """Direct access to the extraction index (for advanced queries)."""
        return self._index

    def write_extraction(
        self,
        *,
        turn_id: str,
        raw_event_id: str,
        summary: str,
        entities: list[dict[str, str]],
        claims: list[str],
        source: str = "",
    ) -> Extraction:
        """Write a per-turn extraction to the index.

        See :meth:`ExtractionIndex.write` for parameter docs.
        """
        return self._index.write(
            turn_id=turn_id,
            raw_event_id=raw_event_id,
            summary=summary,
            entities=entities,
            claims=claims,
            source=source,
        )

    def search(self, query: str, *, limit: int = 5) -> list[ExtractionHit]:
        """Search extractions by keyword. Returns up to *limit* hits."""
        return self._index.search(query, limit=limit)

    def count(self) -> int:
        """Total number of extractions in the index."""
        return self._index.count()

    def close(self) -> None:
        """Release database resources. Idempotent."""
        self._index.close()
