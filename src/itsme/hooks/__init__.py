"""itsme CC/Codex hook entry points (T1.17, T1.17b).

CC plugin hooks.json wires bash shims under ``hooks/cc/`` that pipe the
stdin JSON into ``python -m itsme.hooks <name>``. Each hook reads the CC
payload, opens the events ring, emits ``raw.captured`` with a hook
source label, and exits cleanly. The running MCP server's router then
routes those captures to MemPalace on its background consume loop.

All hook logic lives in pure Python modules so tests can call the same
entry points with a fabricated stdin + injected :class:`EventBus`.
"""
