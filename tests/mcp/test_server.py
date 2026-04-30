"""MCP server wiring — `build_server` registers exactly 3 tools."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core import Memory
from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.events import EventBus
from itsme.mcp.server import SERVER_NAME, build_server


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[Memory]:
    bus = EventBus(db_path=tmp_path / "events.db", capacity=50)
    m = Memory(bus=bus, adapter=InMemoryMemPalaceAdapter(), project="proj")
    yield m
    m.close()


@pytest.mark.asyncio
async def test_server_registers_exactly_three_tools(memory: Memory) -> None:
    """The MCP surface is locked to 3 verbs in v0.0.1."""
    server = build_server(memory)
    tools = await server.list_tools()
    names = sorted(t.name for t in tools)
    assert names == ["ask", "remember", "status"]


def test_server_name_matches_constant(memory: Memory) -> None:
    """Plugin manifest assumes the server name is 'itsme'."""
    server = build_server(memory)
    assert server.name == SERVER_NAME == "itsme"


@pytest.mark.asyncio
async def test_remember_tool_round_trip(memory: Memory) -> None:
    """Calling the registered tool persists a drawer and returns ids."""
    server = build_server(memory)
    raw = await server.call_tool("remember", {"content": "round trip", "kind": "fact"})
    payload = _coerce_dict(raw)
    assert "drawer_id" in payload
    assert payload["wing"] == "wing_proj"
    assert payload["room"] == "room_facts"


@pytest.mark.asyncio
async def test_ask_tool_round_trip(memory: Memory) -> None:
    """ask tool returns the verbatim hit just stored."""
    server = build_server(memory)
    await server.call_tool("remember", {"content": "the magic word is plumage"})
    raw = await server.call_tool("ask", {"question": "plumage"})
    payload = _coerce_dict(raw)
    sources = payload["sources"]
    assert isinstance(sources, list) and sources, "expected at least one verbatim hit"
    first = sources[0]
    assert isinstance(first, dict)
    assert "plumage" in str(first["content"])


@pytest.mark.asyncio
async def test_status_tool_round_trip(memory: Memory) -> None:
    """status tool surfaces the events from previous tool calls."""
    server = build_server(memory)
    await server.call_tool("remember", {"content": "noted"})
    raw = await server.call_tool("status", {"scope": "recent", "format": "json"})
    payload = _coerce_dict(raw)
    count = payload["count"]
    events = payload["events"]
    assert isinstance(count, int) and count >= 2
    assert isinstance(events, list)
    assert {"raw.captured", "memory.stored"} <= {e["type"] for e in events}


def _coerce_dict(raw: object) -> dict[str, object]:
    """FastMCP returns either a dict, a list of ContentBlocks, or a tuple.

    Normalize to the dict view we asserted on inside the handlers.
    """
    if isinstance(raw, dict):
        return raw
    # ``call_tool`` may return ``(unstructured_blocks, structured_dict)``.
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return raw[1]
    # Otherwise it's a sequence of ContentBlock with ``.text`` JSON.
    if isinstance(raw, list | tuple):
        for item in raw:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
    raise AssertionError(f"unexpected tool return shape: {type(raw)!r}")
