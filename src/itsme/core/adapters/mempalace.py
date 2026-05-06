"""MemPalace adapter — verbatim store interface (ARCHITECTURE §7.1).

The rest of itsme talks **only** through this protocol:

* :class:`MemPalaceAdapter` — the contract (write / search / close)
* :class:`InMemoryMemPalaceAdapter` — pure-Python reference impl, used
  by tests and by v0.0.1 first-cut development before the MCP-stdio
  client is wired up.

Writing v0.0.1 against a Protocol means the eventual real backend
(MemPalace MCP server over stdio) drops in without churning the
orchestrator or the MCP tool surface.
"""

from __future__ import annotations

import re
import threading
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID


class MemPalaceWriteResult(BaseModel):
    """Acknowledgement returned by :meth:`MemPalaceAdapter.write`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    drawer_id: str = Field(min_length=1)
    wing: str = Field(min_length=1)
    room: str = Field(min_length=1)


class MemPalaceHit(BaseModel):
    """A single search hit returned by :meth:`MemPalaceAdapter.search`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    drawer_id: str = Field(min_length=1)
    wing: str = Field(min_length=1)
    room: str = Field(min_length=1)
    content: str
    score: float = Field(ge=0.0, le=1.0)


@runtime_checkable
class MemPalaceAdapter(Protocol):
    """The shape every MemPalace backend must satisfy.

    *Synchronous on purpose* — v0.0.1 ``ask`` is a synchronous read; the
    MCP tool layer awaits the answer in-process. Async backends wrap
    themselves in this sync facade.
    """

    def write(
        self,
        *,
        content: str,
        wing: str,
        room: str,
        source_file: str | None = None,
    ) -> MemPalaceWriteResult:
        """Persist a verbatim drawer in *wing*/*room* and return its id."""
        ...

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        wing: str | None = None,
        room: str | None = None,
    ) -> list[MemPalaceHit]:
        """Return up to *limit* hits, optionally scoped to *wing*/*room*."""
        ...

    def close(self) -> None:
        """Release any underlying resources (sockets, subprocesses, ...)."""
        ...


# --------------------------------------------------------------------------
# In-memory reference implementation
# --------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
# CJK Unified Ideographs + extensions A/B + Hiragana + Katakana + Hangul.
# We tokenize each CJK codepoint individually because there is no
# whitespace boundary inside CJK runs, and the default ``\w+`` greedy
# match would swallow an entire sentence as a single token — making
# substring queries like "紫色独角兽" miss a drawer that contains
# "紫色独角兽在月光下吃蓝莓松饼" because the lone token never matches
# the longer one. Per-character tokenization is the cheapest fix that
# keeps the Jaccard scoring honest for short Asian-language queries;
# proper bigram / morphological tokenization belongs in the real
# MemPalace adapter (which uses embeddings anyway).
_CJK_RE = re.compile(
    r"[\u3040-\u309f"  # Hiragana
    r"\u30a0-\u30ff"  # Katakana
    r"\u3400-\u4dbf"  # CJK Extension A
    r"\u4e00-\u9fff"  # CJK Unified Ideographs
    r"\uac00-\ud7af"  # Hangul Syllables
    r"]"
)


def _tokens(text: str) -> set[str]:
    """Split *text* into a token set for Jaccard overlap scoring.

    Latin / Cyrillic / numeric runs use the standard ``\\w+`` boundary;
    CJK characters are emitted one codepoint at a time so that a query
    of N characters can hit a drawer whose CJK run is longer than N.
    """
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        # If the run is entirely CJK, fan it out per-character. Otherwise
        # keep it as a single Latin/numeric token (lowercased).
        cjk_chars = _CJK_RE.findall(raw)
        if cjk_chars and len(cjk_chars) == len(raw):
            out.update(cjk_chars)
        elif cjk_chars:
            # Mixed run (e.g. "v0.0.1版本"): emit both the lowercase whole
            # token (so "版本" → matches the run via shared per-char tokens
            # below) AND each CJK char individually.
            out.add(raw.lower())
            out.update(cjk_chars)
        else:
            out.add(raw.lower())
    return out


class _Drawer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    drawer_id: str
    wing: str
    room: str
    content: str
    source_file: str | None
    tokens: frozenset[str]


class InMemoryMemPalaceAdapter:
    """Deterministic in-memory backend.

    Scoring is deliberately simple — Jaccard overlap of word tokens —
    enough to demonstrate ranking without dragging in embeddings. The
    real MemPalace adapter will replace this whole class.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._drawers: list[_Drawer] = []

    def write(
        self,
        *,
        content: str,
        wing: str,
        room: str,
        source_file: str | None = None,
    ) -> MemPalaceWriteResult:
        if not content.strip():
            raise ValueError("content must be non-empty")
        wing_clean = wing.strip()
        room_clean = room.strip()
        if not wing_clean or not room_clean:
            raise ValueError("wing and room are required")
        drawer = _Drawer(
            drawer_id=str(ULID()),
            wing=wing_clean,
            room=room_clean,
            content=content,
            source_file=source_file,
            tokens=frozenset(_tokens(content)),
        )
        with self._lock:
            self._drawers.append(drawer)
        return MemPalaceWriteResult(
            drawer_id=drawer.drawer_id,
            wing=drawer.wing,
            room=drawer.room,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        wing: str | None = None,
        room: str | None = None,
    ) -> list[MemPalaceHit]:
        if limit <= 0:
            return []
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        with self._lock:
            candidates = list(self._drawers)
        ranked: list[tuple[float, _Drawer]] = []
        for drawer in candidates:
            if wing is not None and drawer.wing != wing:
                continue
            if room is not None and drawer.room != room:
                continue
            if not drawer.tokens:
                continue
            inter = len(q_tokens & drawer.tokens)
            if inter == 0:
                continue
            union = len(q_tokens | drawer.tokens)
            score = inter / union  # Jaccard
            ranked.append((score, drawer))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [
            MemPalaceHit(
                drawer_id=d.drawer_id,
                wing=d.wing,
                room=d.room,
                content=d.content,
                score=score,
            )
            for score, d in ranked[:limit]
        ]

    def close(self) -> None:  # noqa: D401 — Protocol shape
        """No-op for the in-memory backend."""
