"""itsme core — engines, workers, adapters. NOT exposed to agents.

Layout:
- events/    sqlite ring buffer + envelope schema
- workers/   router / promoter / curator / reader
- adapters/  MemPalace adapter (Aleph is in-process at core/aleph)
- aleph/     in-process wiki manager (built from scratch, see §7.2)
- llm.py     LLM provider abstraction (Anthropic first)
- api.py     `Memory` orchestrator (the surface the MCP tools call)
"""

from itsme.core.api import (
    AskResult,
    AskSource,
    Memory,
    RememberResult,
    StatusEvent,
    StatusResult,
    build_default_memory,
    default_db_path,
)

__all__ = [
    "AskResult",
    "AskSource",
    "Memory",
    "RememberResult",
    "StatusEvent",
    "StatusResult",
    "build_default_memory",
    "default_db_path",
]
