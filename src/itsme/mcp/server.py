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

from typing import Any

from itsme.core import Memory, build_default_memory
from itsme.core.config import load_config
from itsme.core.workers import WorkerScheduler
from itsme.mcp.tools.ask import ask_handler
from itsme.mcp.tools.remember import remember_handler
from itsme.mcp.tools.status import status_handler
from mcp.server.fastmcp import FastMCP

SERVER_NAME = "itsme"
SERVER_INSTRUCTIONS = (
    "Long-term memory plugin: 3 verbs. "
    "remember(content, kind?) writes verbatim. "
    "ask(question, mode?) reads memory — mode='auto' (default) "
    "uses dual-engine search (Aleph wiki + MemPalace raw), "
    "mode='wiki' searches Aleph wiki pages only, "
    "mode='verbatim' searches MemPalace only. "
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

    def ask(question: str, mode: str = "auto", limit: int = 5) -> dict[str, Any]:
        """Search memory. mode='auto' (dual-engine) or 'verbatim' (MemPalace only)."""
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
        description="Query memory and return ranked passages.",
    )
    server.add_tool(
        status,
        name="status",
        description="Show recent itsme activity (events ring).",
    )
    return server


def main() -> None:
    """Run the itsme MCP server over stdio.

    Boot sequence:

    1. Load centralised :class:`Config` (env → file → defaults).
    2. Build a :class:`Memory` instance pointed at the events ring.
    3. Spin up a :class:`WorkerScheduler` running the router consume
       loop in a background thread (handles hook-emitted
       ``raw.captured`` events while ``Memory.remember`` keeps its
       sync fast-path).
    4. Register the 3 verbs on a :class:`FastMCP` server.
    5. Run stdio until the host disconnects.

    The function returns normally on clean shutdown so packaging /
    plugin smoke tests can drive it without raising.
    """
    cfg = load_config()
    memory = build_default_memory(cfg=cfg)
    scheduler = WorkerScheduler()
    try:
        # Register the router consume_loop as a background worker so
        # raw.captured events from hooks (source != 'explicit') get
        # routed while the MCP request loop stays free.
        scheduler.add_worker(memory.consume_loop)
        scheduler.start()

        server = build_server(memory)
        server.run("stdio")
    finally:
        # ``stop`` is idempotent and a no-op when start() never reached
        # the alive state, so it's safe to call even if scheduler.start
        # raised mid-boot.
        scheduler.stop()
        memory.close()


if __name__ == "__main__":
    main()
