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


def test_ask_handler_unsupported_mode_bubbles_not_implemented(
    memory: Memory,
) -> None:
    """Documented-but-unimplemented modes get NotImplementedError."""
    with pytest.raises(NotImplementedError):
        ask_handler(memory, question="q", mode="auto")


def test_ask_handler_rejects_non_positive_limit(memory: Memory) -> None:
    """limit must be positive int."""
    with pytest.raises(ValueError):
        ask_handler(memory, question="q", limit=0)


# --------------------------------------------------------- status
def test_status_handler_json_format(memory: Memory) -> None:
    """JSON format mirrors StatusResult."""
    remember_handler(memory, content="x", kind="fact")
    out = status_handler(memory, scope="recent", format="json")
    assert isinstance(out, dict)
    assert "events" in out and "count" in out and "scope" in out


def test_status_handler_feed_format(memory: Memory) -> None:
    """Feed format returns a multi-line string under 'feed' key."""
    remember_handler(memory, content="x", kind="fact")
    out = status_handler(memory, scope="recent", format="feed")
    assert "feed" in out and isinstance(out["feed"], str)
    assert "raw.captured" in out["feed"]


def test_status_handler_rejects_unknown_scope(memory: Memory) -> None:
    """Scope is whitelisted."""
    with pytest.raises(ValueError):
        status_handler(memory, scope="forever")


def test_status_handler_rejects_unknown_format(memory: Memory) -> None:
    """Format is whitelisted."""
    with pytest.raises(ValueError):
        status_handler(memory, format="xml")
