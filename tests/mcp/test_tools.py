"""MCP tool handlers — argument validation + delegation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core import Memory
from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.events import EventBus
from itsme.mcp.tools.ask import ask_handler
from itsme.mcp.tools.remember import remember_handler
from itsme.mcp.tools.status import status_handler


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[Memory]:
    bus = EventBus(db_path=tmp_path / "events.db", capacity=50)
    m = Memory(bus=bus, adapter=InMemoryMemPalaceAdapter(), project="proj")
    yield m
    m.close()


# --------------------------------------------------------- remember
def test_remember_handler_returns_dict(memory: Memory) -> None:
    """Tool handlers must return JSON-serialisable dicts."""
    out = remember_handler(memory, content="hello", kind="fact")
    assert isinstance(out, dict)
    assert {"id", "drawer_id", "wing", "room", "routed_to", "stored_event_id"} <= out.keys()


def test_remember_handler_rejects_empty_content(memory: Memory) -> None:
    """Boundary validation — empty content raises ValueError."""
    with pytest.raises(ValueError):
        remember_handler(memory, content="   ")


def test_remember_handler_rejects_unknown_kind(memory: Memory) -> None:
    """Unknown kind is rejected at the tool boundary (unlike core)."""
    with pytest.raises(ValueError, match="kind"):
        remember_handler(memory, content="x", kind="bogus")


@pytest.mark.parametrize("kind", ["decision", "fact", "feeling", "todo", "event", None])
def test_remember_handler_accepts_valid_kinds(memory: Memory, kind: str | None) -> None:
    """All documented kinds + None are accepted."""
    out = remember_handler(memory, content="ok", kind=kind)
    assert "drawer_id" in out


# --------------------------------------------------------- ask
def test_ask_handler_returns_dict(memory: Memory) -> None:
    """ask returns a JSON-serialisable dict."""
    remember_handler(memory, content="forty two", kind="fact")
    out = ask_handler(memory, question="forty")
    assert isinstance(out, dict)
    assert "answer" in out and "sources" in out and "queried_event_id" in out


def test_ask_handler_rejects_empty_question(memory: Memory) -> None:
    """Boundary validation."""
    with pytest.raises(ValueError):
        ask_handler(memory, question="")


def test_ask_handler_rejects_unknown_mode(memory: Memory) -> None:
    """Mode is whitelisted at the boundary."""
    with pytest.raises(ValueError):
        ask_handler(memory, question="q", mode="exfiltrate")


@pytest.mark.parametrize("mode", ["wiki", "now"])
def test_ask_handler_unsupported_mode_rejected_at_boundary(
    memory: Memory,
    mode: str,
) -> None:
    """Documented-but-unimplemented modes raise ValueError at the tool layer.

    Tool boundary deliberately rejects upfront so callers don't see
    ``NotImplementedError`` leaking from core; the core itself still
    raises NIE for direct callers (verified in tests/core/test_api).
    v0.0.2 supports 'verbatim' and 'auto'; 'wiki' / 'now' are still
    rejected.
    """
    with pytest.raises(ValueError, match="not yet supported"):
        ask_handler(memory, question="q", mode=mode)


def test_ask_handler_rejects_non_positive_limit(memory: Memory) -> None:
    """limit must be positive int."""
    with pytest.raises(ValueError):
        ask_handler(memory, question="q", limit=0)


def test_ask_handler_rejects_oversized_limit(memory: Memory) -> None:
    """limit must respect MAX_LIMIT (defends against runaway queries)."""
    from itsme.mcp.tools.ask import MAX_LIMIT

    with pytest.raises(ValueError, match=str(MAX_LIMIT)):
        ask_handler(memory, question="q", limit=MAX_LIMIT + 1)


# --------------------------------------------------------- status
def test_status_handler_json_format(memory: Memory) -> None:
    """JSON format mirrors StatusResult."""
    remember_handler(memory, content="x", kind="fact")
    out = status_handler(memory, scope="recent", format="json")
    assert isinstance(out, dict)
    assert "events" in out and "count" in out and "scope" in out


def test_status_handler_feed_format(memory: Memory) -> None:
    """Feed format returns a human-readable multi-line string + summary header.

    T1.22 contract: ``feed`` carries one line per event with a typed
    tag (``raw``/``stored``/``routed``/``dedup``/``query``); ``summary``
    is a one-line counts header for the IDE to render at the top.
    """
    remember_handler(memory, content="x", kind="fact")
    out = status_handler(memory, scope="recent", format="feed")
    assert "feed" in out and isinstance(out["feed"], str)
    assert "summary" in out and isinstance(out["summary"], str)
    # One remember produces raw + routed + stored — all should appear.
    assert "raw" in out["feed"]
    assert "route" in out["feed"]
    assert "store" in out["feed"]
    # Summary line tallies what fired.
    assert "raw" in out["summary"] and "store" in out["summary"]


def test_status_handler_rejects_unknown_scope(memory: Memory) -> None:
    """Scope is whitelisted."""
    with pytest.raises(ValueError):
        status_handler(memory, scope="forever")


def test_status_handler_rejects_unknown_format(memory: Memory) -> None:
    """Format is whitelisted."""
    with pytest.raises(ValueError):
        status_handler(memory, format="xml")


def test_status_handler_rejects_non_positive_limit(memory: Memory) -> None:
    """limit must be a positive int (defensive — Memory also checks)."""
    with pytest.raises(ValueError):
        status_handler(memory, limit=0)


def test_status_handler_rejects_oversized_limit(memory: Memory) -> None:
    """limit must respect MAX_LIMIT (bounds JSON / feed payload size)."""
    from itsme.mcp.tools.status import MAX_LIMIT

    with pytest.raises(ValueError, match=str(MAX_LIMIT)):
        status_handler(memory, limit=MAX_LIMIT + 1)


# --------------------------------------------------------- defensive type checks
def test_ask_handler_rejects_unhashable_mode(memory: Memory) -> None:
    """mode=[] must ValueError, not TypeError on the set membership check."""
    with pytest.raises(ValueError, match="mode"):
        ask_handler(memory, question="q", mode=[])  # type: ignore[arg-type]


def test_ask_handler_rejects_bool_limit(memory: Memory) -> None:
    """``limit=True`` is a programming error, not '1 hit'."""
    with pytest.raises(ValueError, match="limit"):
        ask_handler(memory, question="q", limit=True)


def test_status_handler_rejects_bool_limit(memory: Memory) -> None:
    """``limit=True`` is a programming error, not '1 event'."""
    with pytest.raises(ValueError, match="limit"):
        status_handler(memory, limit=True)


def test_remember_handler_rejects_non_string_kind(memory: Memory) -> None:
    """``kind=123`` is a programming error, not silent fall-through."""
    with pytest.raises(ValueError, match="kind"):
        remember_handler(memory, content="x", kind=123)  # type: ignore[arg-type]
