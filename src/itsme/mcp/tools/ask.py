"""``ask(question, mode?)`` — query tool (T1.11).

Tool-layer responsibility: argument validation + orchestration only.
We do **not** call MemPalace MCP or Aleph internals here; everything
goes through :class:`itsme.core.Memory` so the read path stays
swappable.

v0.0.1 honors only ``mode='verbatim'``; ``mode='auto'`` and
``promote=true`` arrive in v0.0.2 / v0.0.3 (see ROADMAP).
"""

from __future__ import annotations

from typing import Any, Literal, cast

from itsme.core import Memory

#: Hard upper bound on a single ``ask`` so a malicious or buggy caller
#: can't ask MemPalace for thousands of hits and pin the bus.
MAX_LIMIT = 100


def ask_handler(
    memory: Memory,
    *,
    question: str,
    mode: str = "verbatim",
    limit: int = 5,
) -> dict[str, Any]:
    """Validate inputs and dispatch to :meth:`Memory.ask`.

    Args:
        memory: Process-wide :class:`Memory` instance.
        question: Natural-language query. Must be non-empty.
        mode: Read strategy. v0.0.1 only accepts ``"verbatim"``.
        limit: Max number of hits (1 ≤ limit ≤ :data:`MAX_LIMIT`).

    Returns:
        Plain-dict view of :class:`itsme.core.AskResult`.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    if not isinstance(mode, str) or mode not in {"verbatim", "auto", "wiki", "now"}:
        raise ValueError(f"mode must be one of 'verbatim' / 'auto' / 'wiki' / 'now'; got {mode!r}")
    # bool is a subclass of int in Python — reject it explicitly so
    # ``limit=True`` doesn't silently mean "1 hit".
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if limit > MAX_LIMIT:
        raise ValueError(f"limit must be a positive integer and <= {MAX_LIMIT}; got {limit}")

    result = memory.ask(
        question=question,
        mode=cast(Literal["verbatim", "auto", "wiki", "now"], mode),
        limit=limit,
    )
    return result.model_dump(mode="json")
