"""``remember(content, kind?)`` — explicit write tool (T1.10).

Argument validation only; orchestration lives in :class:`itsme.core.Memory`.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from itsme.core import Memory


def remember_handler(
    memory: Memory,
    *,
    content: str,
    kind: str | None = None,
) -> dict[str, Any]:
    """Validate inputs and dispatch to :meth:`Memory.remember`.

    Args:
        memory: Process-wide :class:`Memory` instance.
        content: Verbatim text to store.
        kind: Optional hint — one of ``decision`` / ``fact`` / ``feeling``
            / ``todo`` / ``event``. Anything else is rejected at the
            tool boundary so callers get an immediate error instead of
            silent fall-through to ``general``.

    Returns:
        Plain-dict view of :class:`itsme.core.RememberResult`.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")

    valid_kinds = {"decision", "fact", "feeling", "todo", "event"}
    if kind is not None and (not isinstance(kind, str) or kind not in valid_kinds):
        raise ValueError(f"kind must be one of {sorted(valid_kinds)} or omitted; got {kind!r}")

    typed_kind = cast(
        Literal["decision", "fact", "feeling", "todo", "event"] | None,
        kind,
    )
    result = memory.remember(content=content, kind=typed_kind)
    return result.model_dump(mode="json")
