"""External engine adapters. Currently: MemPalace MCP wrapper.

Aleph is in-process (core/aleph), so it is NOT here.
"""

from itsme.core.adapters.mempalace import (
    InMemoryMemPalaceAdapter,
    MemPalaceAdapter,
    MemPalaceHit,
    MemPalaceWriteResult,
)
from itsme.core.adapters.mempalace_stdio import (
    DEFAULT_CALL_TIMEOUT_S,
    DEFAULT_COMMAND,
    DEFAULT_HANDSHAKE_TIMEOUT_S,
    MemPalaceConnectError,
    MemPalaceWriteError,
    StdioMemPalaceAdapter,
)
from itsme.core.adapters.naming import room, wing

__all__ = [
    "DEFAULT_CALL_TIMEOUT_S",
    "DEFAULT_COMMAND",
    "DEFAULT_HANDSHAKE_TIMEOUT_S",
    "InMemoryMemPalaceAdapter",
    "MemPalaceAdapter",
    "MemPalaceConnectError",
    "MemPalaceHit",
    "MemPalaceWriteError",
    "MemPalaceWriteResult",
    "StdioMemPalaceAdapter",
    "room",
    "wing",
]
