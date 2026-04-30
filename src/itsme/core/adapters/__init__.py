"""External engine adapters. Currently: MemPalace MCP wrapper.

Aleph is in-process (core/aleph), so it is NOT here.
"""

from itsme.core.adapters.mempalace import (
    InMemoryMemPalaceAdapter,
    MemPalaceAdapter,
    MemPalaceHit,
    MemPalaceWriteResult,
)
from itsme.core.adapters.naming import room, wing

__all__ = [
    "InMemoryMemPalaceAdapter",
    "MemPalaceAdapter",
    "MemPalaceHit",
    "MemPalaceWriteResult",
    "room",
    "wing",
]
