"""``ask(question, mode?)`` — query tool (T1.11 + T3.0).

Tool-layer responsibility: argument validation + orchestration only.
We do **not** call MemPalace MCP or Aleph internals here; everything
goes through :class:`itsme.core.Memory` so the read path stays
swappable.

v0.0.1 honored only ``mode='verbatim'``; v0.0.2 added ``mode='auto'``
for dual-engine search (Vault wiki + MemPalace raw). T3.0 removed the
SQLite FTS5 extraction layer — search is now wiki + MemPalace.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from itsme.core import Memory

#: Hard upper bound on a single ``ask`` so a malicious or buggy caller
#: can't ask MemPalace for thousands of hits and pin the bus.
MAX_LIMIT = 100

#: Modes accepted at the tool boundary in v0.0.2.
_ACCEPTED_MODES = {"verbatim", "auto"}


def ask_handler(
    memory: Memory,
    *,
    question: str,
    mode: str = "auto",
    limit: int = 5,
) -> dict[str, Any]:
    """Validate inputs and dispatch to :meth:`Memory.ask`.

    Args:
        memory: Process-wide :class:`Memory` instance.
        question: Natural-language query. Must be non-empty.
        mode: Read strategy. v0.0.2 accepts ``"verbatim"`` and
            ``"auto"`` (dual-engine search).
        limit: Max number of hits (1 ≤ limit ≤ :data:`MAX_LIMIT`).

    Returns:
        Plain-dict view of :class:`itsme.core.AskResult`.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    if not isinstance(mode, str):
        raise ValueError(f"mode must be a string; got {mode!r}")
    if mode in _ACCEPTED_MODES:
        pass
    elif mode in {"wiki", "now"}:
        raise ValueError(
            f"mode={mode!r} is not yet supported in v0.0.2 — "
            "only 'verbatim' and 'auto' are available"
        )
    else:
        raise ValueError(f"mode must be one of 'verbatim' / 'auto' / 'wiki' / 'now'; got {mode!r}")

    # bool is a subclass of int in Python — reject it explicitly so
    # ``limit=True`` doesn't silently mean "1 hit".
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if limit > MAX_LIMIT:
        raise ValueError(f"limit must be a positive integer and <= {MAX_LIMIT}; got {limit}")

    result = memory.ask(
        question=question,
        mode=cast(Literal["verbatim", "auto"], mode),
        limit=limit,
    )
    return result.model_dump(mode="json")
