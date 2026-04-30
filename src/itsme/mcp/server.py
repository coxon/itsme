"""itsme MCP server entrypoint (T1.9).

Launched by ``plugin.json`` via ``python -m itsme.mcp.server``. Wires
the 3-verb surface (``remember`` / ``ask`` / ``status``) onto a
:class:`itsme.core.Memory` instance and runs over stdio.

Design:

* The MCP layer is **thin**. Tools call the matching handler in
  ``itsme.mcp.tools.*`` which immediately delegates to ``itsme.core``.
* No I/O outside ``main()`` — all wiring is lazy so importing this
  module has no side-effects (tests rely on this).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from itsme.core import Memory, build_default_memory, default_db_path
from itsme.mcp.tools.ask import ask_handler
from itsme.mcp.tools.remember import remember_handler
from itsme.mcp.tools.status import status_handler
from mcp.server.fastmcp import FastMCP

SERVER_NAME = "itsme"
SERVER_INSTRUCTIONS = (
    "Long-term memory plugin: 3 verbs. "
    "remember(content, kind?) writes verbatim. "
    "ask(question, mode?) reads verbatim (v0.0.1). "
    "status(scope?, format?) shows recent activity."
)


def build_server(memory: Memory) -> FastMCP[Any]:
    """Construct a configured FastMCP server bound to *memory*.

    Split out from :func:`main` so tests can introspect the registered
    tools without spinning up stdio.
    """
    server: FastMCP[Any] = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

    def remember(content: str, kind: str | None = None) -> dict[str, Any]:
        """Persist a memory drawer. Returns the new event id + drawer id."""
        return remember_handler(memory, content=content, kind=kind)

    def ask(question: str, mode: str = "verbatim", limit: int = 5) -> dict[str, Any]:
        """Search verbatim memory. v0.0.1 only supports mode='verbatim'."""
        return ask_handler(memory, question=question, mode=mode, limit=limit)

    def status(
        scope: str = "recent",
        format: str = "json",
        limit: int = 20,  # noqa: A002
    ) -> dict[str, Any]:
        """Surface recent events from the bus ring."""
        return status_handler(memory, scope=scope, format=format, limit=limit)

    server.add_tool(
        remember,
        name="remember",
        description="Save a verbatim memory; optionally hint kind ∈ "
        "{decision, fact, feeling, todo, event}.",
    )
    server.add_tool(
        ask,
        name="ask",
        description="Query verbatim memory and return ranked passages.",
    )
    server.add_tool(
        status,
        name="status",
        description="Show recent itsme activity (events ring).",
    )
    return server


def _resolve_db_path() -> Path:
    """Honor ``$ITSME_DB_PATH`` if set, else fall back to the shared default."""
    raw = os.environ.get("ITSME_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return default_db_path()


def _resolve_project() -> str:
    """Honor ``$ITSME_PROJECT`` if set, else 'default'."""
    return os.environ.get("ITSME_PROJECT", "default")


def main() -> None:
    """Run the itsme MCP server over stdio.

    Boot sequence:

    1. Build a :class:`Memory` instance pointed at the events ring.
    2. Register the 3 verbs on a :class:`FastMCP` server.
    3. Run stdio until the host disconnects.

    The function returns normally on clean shutdown so packaging /
    plugin smoke tests can drive it without raising.
    """
    memory = build_default_memory(
        project=_resolve_project(),
        db_path=_resolve_db_path(),
    )
    try:
        server = build_server(memory)
        server.run("stdio")
    finally:
        memory.close()


if __name__ == "__main__":
    main()
