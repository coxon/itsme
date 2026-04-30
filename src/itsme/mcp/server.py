"""itsme MCP server entrypoint (v0.0.1 stub).

Launched via plugin.json: `python -m itsme.mcp.server`.

Real implementation: T1.9 — register 3 tools (remember / ask / status)
with the MCP SDK and dispatch to `itsme.core.*`.
"""

from __future__ import annotations


def main() -> None:
    """Run the itsme MCP server (stub until T1.9).

    Raises:
        NotImplementedError: always, until the MCP tool surface is wired
            up in v0.0.1 T1.9. The stub keeps the `python -m
            itsme.mcp.server` invocation path reachable so plugin.json
            and packaging can be validated before any real behavior.
    """
    raise NotImplementedError(
        "itsme MCP server — implement in v0.0.1 T1.9 "
        "(see docs/ROADMAP.md and ARCHITECTURE.md §4)"
    )


if __name__ == "__main__":
    main()
