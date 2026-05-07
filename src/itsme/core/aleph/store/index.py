"""Aleph extraction index — T2.1.

Lightweight sqlite + FTS5 store for per-turn LLM extractions. This is
Aleph's v0.0.2 incarnation: no wiki entries, no vault, no embedding —
just a fast keyword-searchable index of structured extractions that
serves as the high-precision search surface alongside MemPalace's raw
high-recall surface.

Schema::

    extractions
    ├── id           TEXT PK   (ULID)
    ├── turn_id      TEXT      (MemPalace drawer_id for this turn)
    ├── raw_event_id TEXT      (EventBus event id of the raw.captured)
    ├── summary      TEXT      (one-sentence summary)
    ├── entities     TEXT      (JSON array: [{name, type, role?}])
    ├── claims       TEXT      (JSON array: ["claim1", "claim2"])
    ├── source       TEXT      ("hook:before-exit" / "hook:context-pressure")
    └── created_at   REAL      (unix timestamp)

    extractions_fts (FTS5 virtual table)
    ├── summary
    ├── entities_text          (flattened entity names for FTS)
    └── claims_text            (flattened claims for FTS)

Thread safety: all writes and reads go through a ``threading.Lock``
so the index can be shared between the intake worker thread and the
reader (MCP ask handler) without external synchronisation.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ulid import ULID

# --------------------------------------------------------------------- schema

_SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS extractions (
    id           TEXT PRIMARY KEY,
    turn_id      TEXT,
    raw_event_id TEXT,
    summary      TEXT NOT NULL,
    entities     TEXT NOT NULL DEFAULT '[]',
    claims       TEXT NOT NULL DEFAULT '[]',
    source       TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS extractions_fts USING fts5(
    summary,
    entities_text,
    claims_text,
    content=extractions,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync with the content table.
CREATE TRIGGER IF NOT EXISTS extractions_ai AFTER INSERT ON extractions BEGIN
    INSERT INTO extractions_fts(rowid, summary, entities_text, claims_text)
    VALUES (new.rowid, new.summary, new.entities, new.claims);
END;

CREATE TRIGGER IF NOT EXISTS extractions_ad AFTER DELETE ON extractions BEGIN
    INSERT INTO extractions_fts(extractions_fts, rowid, summary, entities_text, claims_text)
    VALUES ('delete', old.rowid, old.summary, old.entities, old.claims);
END;
"""

# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class Extraction:
    """A single per-turn extraction record."""

    id: str
    turn_id: str
    raw_event_id: str
    summary: str
    entities: list[dict[str, str]]
    claims: list[str]
    source: str
    created_at: float


@dataclass(frozen=True)
class ExtractionHit:
    """A search result from the extraction index."""

    extraction: Extraction
    rank: float = 0.0  # FTS5 rank (lower = better match)


# --------------------------------------------------------------------- index


class ExtractionIndex:
    """SQLite + FTS5 backed extraction store.

    Args:
        db_path: Path to the sqlite database file. The directory is
            created if it doesn't exist. Use ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()

        if db_path != ":memory:":
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,  # we manage thread safety via _lock
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_SQL)

    # ----------------------------------------------------------- write

    def write(
        self,
        *,
        turn_id: str,
        raw_event_id: str,
        summary: str,
        entities: list[dict[str, str]],
        claims: list[str],
        source: str = "",
        created_at: float | None = None,
    ) -> Extraction:
        """Insert a new extraction and return it.

        Args:
            turn_id: MemPalace drawer_id for the turn.
            raw_event_id: EventBus event id of the ``raw.captured``.
            summary: One-sentence summary of the turn.
            entities: List of ``{name, type, role?}`` dicts.
            claims: List of claim strings.
            source: Producer label (e.g. ``"hook:before-exit"``).
            created_at: Unix timestamp; defaults to now (from ULID).

        Returns:
            The persisted :class:`Extraction`.
        """
        extraction_id = str(ULID())
        ts = created_at if created_at is not None else ULID().timestamp

        entities_json = json.dumps(entities, ensure_ascii=False)
        claims_json = json.dumps(claims, ensure_ascii=False)

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO extractions
                    (id, turn_id, raw_event_id, summary, entities, claims, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    extraction_id,
                    turn_id,
                    raw_event_id,
                    summary,
                    entities_json,
                    claims_json,
                    source,
                    ts,
                ),
            )
            self._conn.commit()

        return Extraction(
            id=extraction_id,
            turn_id=turn_id,
            raw_event_id=raw_event_id,
            summary=summary,
            entities=entities,
            claims=claims,
            source=source,
            created_at=ts,
        )

    # ----------------------------------------------------------- search

    def search(self, query: str, *, limit: int = 5) -> list[ExtractionHit]:
        """FTS5 keyword search over summaries, entities, and claims.

        Returns at most *limit* hits, ordered by FTS5 rank (best first).
        An empty or whitespace-only *query* returns ``[]``.
        """
        if not query or not query.strip():
            return []
        if limit <= 0:
            return []

        # FTS5 query: use implicit AND between terms. Double-quote the
        # query to handle special chars (colons, hyphens in entity names).
        # Fallback: if quoting fails, try the raw query.
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []

        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT e.*, rank
                    FROM extractions_fts
                    JOIN extractions e ON e.rowid = extractions_fts.rowid
                    WHERE extractions_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # Malformed FTS query — degrade to empty.
                return []

        hits: list[ExtractionHit] = []
        for row in rows:
            entities = _safe_json_list(row["entities"])
            claims = _safe_json_list(row["claims"])
            extraction = Extraction(
                id=row["id"],
                turn_id=row["turn_id"],
                raw_event_id=row["raw_event_id"],
                summary=row["summary"],
                entities=[e for e in entities if isinstance(e, dict)],
                claims=[c for c in claims if isinstance(c, str)],
                source=row["source"],
                created_at=row["created_at"],
            )
            hits.append(ExtractionHit(extraction=extraction, rank=row["rank"]))
        return hits

    # ----------------------------------------------------------- count

    def count(self) -> int:
        """Return the total number of extractions."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM extractions").fetchone()
            return row[0] if row else 0

    # ----------------------------------------------------------- close

    def close(self) -> None:
        """Close the database connection. Idempotent."""
        with self._lock:
            self._conn.close()


# --------------------------------------------------------------------- helpers


def _build_fts_query(query: str) -> str:
    """Build an FTS5 query string from a natural-language query.

    Splits on whitespace, wraps each token in quotes (to handle
    hyphens, dots, etc.), and joins with implicit AND.
    """
    tokens = query.strip().split()
    if not tokens:
        return ""
    # Each token quoted so FTS5 treats hyphens/dots as literals.
    return " ".join(f'"{t}"' for t in tokens)


def _safe_json_list(raw: str | None) -> list:
    """Parse a JSON array string, returning [] on any failure."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
