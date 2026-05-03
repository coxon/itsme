"""Minimal in-process MemPalace stand-in for StdioMemPalaceAdapter tests.

Speaks just enough JSON-RPC 2.0 over stdin/stdout to exercise the
adapter's framing, error handling, duplicate logic, and timeout paths
without requiring a real MemPalace install on every CI runner.

Usage (from tests)::

    python -m tests.core.adapters.fake_mempalace_server [--mode MODE]

where MODE is:

* ``normal`` (default) — handshake + add_drawer (with dupe detection) +
  search behave like MemPalace.
* ``slow-handshake`` — sleeps ``FAKE_MP_SLEEP`` seconds before replying
  to ``initialize``. Used to drive the handshake-timeout path.
* ``slow-call`` — sleeps ``FAKE_MP_SLEEP`` before every ``tools/call``.
* ``bad-json`` — emits a malformed line in response to the first
  ``tools/call`` to exercise the parser error path.
* ``die-on-first-call`` — exits 1 after ``initialize`` succeeds, on the
  first ``tools/call`` write. Drives the "subprocess gone mid-call"
  branch.

Kept intentionally small — ``_DRAWERS`` is a process-local list; the
real MemPalace has ChromaDB, wings, and rooms as first-class artifacts
but for unit tests we only need stable round-trip behaviour.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

_MODE = "normal"
_SLEEP_S = 0.0
_DRAWERS: list[dict[str, str]] = []


def _emit(obj: dict) -> None:
    """Write one JSON line + flush (NDJSON framing)."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _log(msg: str) -> None:
    """Log to stderr so the adapter's drain thread picks it up."""
    sys.stderr.write(f"[fake-mp] {msg}\n")
    sys.stderr.flush()


def _wrap_result(payload: dict) -> list[dict[str, str]]:
    """Emit MemPalace's ``content: [{type: text, text: <json>}]`` shape."""
    return [{"type": "text", "text": json.dumps(payload)}]


def _handle_initialize(rid: int | str) -> None:
    if _MODE == "slow-handshake":
        time.sleep(_SLEEP_S)
    _emit(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mempalace", "version": "0.0.1"},
            },
        }
    )


def _handle_add_drawer(args: dict) -> dict:
    wing = args.get("wing", "")
    room = args.get("room", "")
    content = args.get("content", "")
    # Stable id keyed on content — duplicates reuse this.
    digest = hashlib.sha256(f"{wing}|{room}|{content}".encode()).hexdigest()[:16]
    drawer_id = f"fake-{digest}"
    for existing in _DRAWERS:
        if existing["drawer_id"] == drawer_id:
            return {
                "success": False,
                "reason": "duplicate",
                "matches": [
                    {
                        "id": drawer_id,
                        "wing": wing,
                        "room": room,
                        "content": content,
                    }
                ],
            }
    _DRAWERS.append({"drawer_id": drawer_id, "wing": wing, "room": room, "content": content})
    return {"success": True, "drawer_id": drawer_id, "wing": wing, "room": room}


def _handle_search(args: dict) -> dict:
    query = args.get("query", "").lower()
    limit = int(args.get("limit", 5))
    wing = args.get("wing")
    room = args.get("room")
    # Two error escape hatches keyed off magic wings, used by
    # test_mempalace_stdio.py to drive the search() error branches:
    # * ``__no_palace__`` → benign "No palace found" → search() == []
    # * ``__boom__``      → unrelated error string → search() raises
    if wing == "__no_palace__":
        return {"error": "No palace found at ~/.mempalace/default"}
    if wing == "__boom__":
        return {"error": "ChromaDB exploded mid-query"}
    # Cheap substring scoring so the tests can assert deterministic order.
    scored: list[tuple[float, dict]] = []
    for d in _DRAWERS:
        if wing and d["wing"] != wing:
            continue
        if room and d["room"] != room:
            continue
        # Jaccard-ish token overlap; enough for deterministic scoring.
        q_tokens = set(query.split())
        d_tokens = set(d["content"].lower().split())
        if not q_tokens or not d_tokens:
            continue
        overlap = len(q_tokens & d_tokens)
        if overlap == 0:
            continue
        score = overlap / len(q_tokens | d_tokens)
        scored.append(
            (
                score,
                {
                    "text": d["content"],
                    "wing": d["wing"],
                    "room": d["room"],
                    "similarity": score,
                },
            )
        )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return {"results": [hit for _, hit in scored[:limit]]}


def _handle_tools_call(rid: int | str, params: dict) -> None:
    global _MODE  # noqa: PLW0603 — mode state is intentional for this fake

    if _MODE == "slow-call":
        time.sleep(_SLEEP_S)

    if _MODE == "die-on-first-call":
        _log("die-on-first-call: exiting")
        sys.exit(1)

    if _MODE == "bad-json":
        # One-shot: flip back so subsequent calls work normally.
        _MODE = "normal"
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        return

    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if name == "mempalace_add_drawer":
        payload = _handle_add_drawer(arguments)
    elif name == "mempalace_search":
        payload = _handle_search(arguments)
    elif name == "mempalace_raise_error":
        # Test hook: simulate an upstream tool error.
        _emit(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32000, "message": "boom"},
            }
        )
        return
    elif name == "mempalace_empty_palace":
        payload = {"error": "No palace found at ~/.mempalace/default"}
    else:
        _emit(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"unknown tool {name!r}"},
            }
        )
        return

    _emit(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": _wrap_result(payload)},
        }
    )


def main() -> int:
    global _MODE, _SLEEP_S  # noqa: PLW0603

    _MODE = os.environ.get("FAKE_MP_MODE", "normal")
    try:
        _SLEEP_S = float(os.environ.get("FAKE_MP_SLEEP", "2.0"))
    except ValueError:
        _SLEEP_S = 2.0

    _log(f"booted mode={_MODE} sleep={_SLEEP_S}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _log(f"ignoring non-JSON input: {line!r}")
            continue

        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            _handle_initialize(rid)
        elif method == "notifications/initialized":
            # Fire-and-forget. Nothing to do.
            _log("got notifications/initialized")
        elif method == "tools/call":
            _handle_tools_call(rid, params)
        elif rid is not None:
            _emit(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"method {method!r} not found"},
                }
            )
        # else: unknown notification, ignore silently like a real server

    _log("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
