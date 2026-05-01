"""Hook CLI entry — ``python -m itsme.hooks <hook-name>``.

Dispatch table:

=====================  ==========  ==========================================
hook-name              source      handler
=====================  ==========  ==========================================
before-exit            SessionEnd  :func:`lifecycle.run_lifecycle_hook`
before-compact         PreCompact  :func:`lifecycle.run_lifecycle_hook`
context-pressure       Prompt/Tool :func:`context_pressure.run_context_pressure`
=====================  ==========  ==========================================

Error policy: **never exit non-zero on capture failures**. CC surfaces
non-zero hooks as red errors in the UI, which is wrong for a passive
plugin. Bad input or internal failures get logged to stderr and the
process exits 0. Only *usage* errors (wrong argv, unknown hook name)
exit 2 — those are developer bugs, not runtime events.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Final

from itsme.core.events import EventBus
from itsme.hooks import _common
from itsme.hooks.context_pressure import run_context_pressure
from itsme.hooks.lifecycle import run_lifecycle_hook

_logger = logging.getLogger(__name__)

_USAGE: Final = "usage: python -m itsme.hooks <before-exit|before-compact|context-pressure>"


def _dispatch(name: str, stdin_text: str, bus: EventBus, state_dir: Path) -> dict[str, Any]:
    """Route *name* to its handler. Returns the CC hook output dict."""
    if name == "before-exit":
        return run_lifecycle_hook(stdin_text, bus=bus, source="hook:before-exit")
    if name == "before-compact":
        return run_lifecycle_hook(stdin_text, bus=bus, source="hook:before-compact")
    if name == "context-pressure":
        return run_context_pressure(stdin_text, bus=bus, state_dir=state_dir)
    raise SystemExit(f"unknown hook: {name!r}\n{_USAGE}")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns process exit code.

    Exposed as a function so tests can drive it without ``sys.exit``.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    name = args[0]
    if name not in {"before-exit", "before-compact", "context-pressure"}:
        print(f"unknown hook: {name!r}\n{_USAGE}", file=sys.stderr)
        return 2

    stdin_text = sys.stdin.read()

    # Short-circuit the disabled flag BEFORE touching the events ring.
    # Otherwise a user who sets ITSME_HOOKS_DISABLED=1 *and* has an
    # unwritable db path still gets stderr spam from open_bus().
    if _common.hooks_disabled():
        json.dump(_common.ok_output(), sys.stdout)
        sys.stdout.write("\n")
        return 0

    # Open bus lazily — if the ring db path isn't writable we log and
    # exit 0 so the hook doesn't surface as a UI error.
    bus: EventBus | None = None
    try:
        bus = _common.open_bus()
    except Exception as exc:  # pragma: no cover — filesystem edge case
        print(f"itsme hook {name}: failed to open events ring: {exc}", file=sys.stderr)
        return 0

    try:
        out = _dispatch(name, stdin_text, bus, _common.resolve_state_dir())
    except ValueError as exc:
        # Malformed stdin — log but don't block the IDE.
        print(f"itsme hook {name}: bad input: {exc}", file=sys.stderr)
        return 0
    except Exception:  # noqa: BLE001 - last-resort barrier at process edge
        print(f"itsme hook {name}: unhandled error", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 0
    finally:
        # Close must not escape: CC treats non-zero exits as UI errors,
        # and a SQLite WAL checkpoint failure here would otherwise mask
        # the ``return 0`` above. Guard against ``bus is None`` so a
        # future refactor that lets the open path skip assignment can't
        # raise UnboundLocalError on the way out.
        if bus is not None:
            try:
                bus.close()
            except Exception as exc:  # pragma: no cover - process-edge barrier
                print(f"itsme hook {name}: failed to close events ring: {exc}", file=sys.stderr)

    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
