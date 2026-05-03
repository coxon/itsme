"""Unit tests for StdioMemPalaceAdapter — drives the fake server over stdio.

Coverage matrix:

* construction + clean shutdown
* write happy path
* write duplicate → success with reused id
* write error → MemPalaceWriteError
* search happy path ordered by similarity
* search empty-palace error response → []
* search wing/room filter
* tools/call JSON-RPC error → MemPalaceConnectError
* malformed JSON response → MemPalaceConnectError
* handshake timeout → MemPalaceConnectError + subprocess cleaned up
* per-call timeout → MemPalaceConnectError
* subprocess dies mid-call → MemPalaceConnectError
* missing executable → MemPalaceConnectError at __init__
* env-var factory honours ITSME_MEMPALACE_* vars

These run without the real MemPalace binary; see
``tests/smoke/test_mempalace_stdio_roundtrip.py`` for the real-binary
version.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator

import pytest

from itsme.core.adapters.mempalace import MemPalaceHit, MemPalaceWriteResult
from itsme.core.adapters.mempalace_stdio import (
    MemPalaceConnectError,
    MemPalaceWriteError,
    StdioMemPalaceAdapter,
)

# The fake server is ``python -m tests.core.adapters.fake_mempalace_server``.
# We drive it via the current interpreter so there's no PATH dance.
FAKE_CMD: tuple[str, ...] = (
    sys.executable,
    "-m",
    "tests.core.adapters.fake_mempalace_server",
)


def _spawn(
    *,
    mode: str = "normal",
    sleep_s: float = 2.0,
    handshake_timeout_s: float = 5.0,
    call_timeout_s: float = 5.0,
) -> StdioMemPalaceAdapter:
    """Start an adapter attached to the fake server in *mode*."""
    env = dict(os.environ)
    env["FAKE_MP_MODE"] = mode
    env["FAKE_MP_SLEEP"] = str(sleep_s)
    # Ensure the test runner's project root is importable in the child.
    env.setdefault("PYTHONPATH", os.pathsep.join(sys.path))
    return StdioMemPalaceAdapter(
        command=FAKE_CMD,
        handshake_timeout_s=handshake_timeout_s,
        call_timeout_s=call_timeout_s,
        env=env,
    )


@pytest.fixture
def adapter() -> Iterator[StdioMemPalaceAdapter]:
    """A live adapter pointed at the fake server in ``normal`` mode."""
    a = _spawn()
    yield a
    a.close()


# --------------------------------------------------------------- lifecycle


def test_construction_boots_subprocess() -> None:
    """__init__ spawns the child and completes the handshake."""
    a = _spawn()
    try:
        # If the handshake didn't complete we'd have raised already.
        # Prove the pipe is live with a trivial tool call.
        hits = a.search("anything")
        assert hits == []  # empty palace, zero drawers yet
    finally:
        a.close()


def test_close_is_idempotent(adapter: StdioMemPalaceAdapter) -> None:
    """Double-close must not raise."""
    adapter.close()
    adapter.close()  # no crash


def test_missing_executable_raises_connect_error() -> None:
    """An argv pointing at a missing binary fails loud at __init__."""
    with pytest.raises(MemPalaceConnectError, match="not found on PATH"):
        StdioMemPalaceAdapter(command=("itsme-absolutely-nonexistent-bin",))


# --------------------------------------------------------------- write


def test_write_happy_path(adapter: StdioMemPalaceAdapter) -> None:
    """A fresh write returns a drawer_id and preserves wing/room."""
    result = adapter.write(content="Persist me", wing="itsme", room="decisions")
    assert isinstance(result, MemPalaceWriteResult)
    assert result.drawer_id
    assert result.wing == "itsme"
    assert result.room == "decisions"


def test_write_duplicate_is_idempotent(adapter: StdioMemPalaceAdapter) -> None:
    """Re-writing the same content returns the same drawer_id (dedup)."""
    first = adapter.write(content="same thing", wing="itsme", room="decisions")
    second = adapter.write(content="same thing", wing="itsme", room="decisions")
    assert second.drawer_id == first.drawer_id


def test_write_rejects_empty_content(adapter: StdioMemPalaceAdapter) -> None:
    """Client-side validation: empty content never reaches the subprocess."""
    with pytest.raises(ValueError, match="content"):
        adapter.write(content="  ", wing="itsme", room="decisions")


def test_write_rejects_empty_wing_or_room(adapter: StdioMemPalaceAdapter) -> None:
    with pytest.raises(ValueError, match="wing and room"):
        adapter.write(content="x", wing="", room="decisions")
    with pytest.raises(ValueError, match="wing and room"):
        adapter.write(content="x", wing="itsme", room=" ")


# --------------------------------------------------------------- search


def test_search_orders_by_similarity(adapter: StdioMemPalaceAdapter) -> None:
    """Higher token overlap → higher score → earlier in the list."""
    adapter.write(content="alpha beta gamma", wing="w", room="r")
    adapter.write(content="alpha only", wing="w", room="r")
    adapter.write(content="something unrelated", wing="w", room="r")
    hits = adapter.search("alpha beta", limit=5)
    assert len(hits) == 2  # "unrelated" shares no tokens
    assert "alpha beta gamma" in hits[0].content  # higher overlap first
    # Pseudo-ids are stable and identifiable.
    assert all(h.drawer_id.startswith("mp-search:w:r:") for h in hits)
    assert all(isinstance(h, MemPalaceHit) for h in hits)
    assert all(0.0 <= h.score <= 1.0 for h in hits)


def test_search_respects_wing_filter(adapter: StdioMemPalaceAdapter) -> None:
    adapter.write(content="hello world", wing="one", room="r")
    adapter.write(content="hello world", wing="two", room="r")
    one_only = adapter.search("hello", wing="one")
    assert len(one_only) == 1
    assert one_only[0].wing == "one"


def test_search_respects_room_filter(adapter: StdioMemPalaceAdapter) -> None:
    adapter.write(content="hello world", wing="w", room="a")
    adapter.write(content="hello world", wing="w", room="b")
    only_a = adapter.search("hello", room="a")
    assert len(only_a) == 1
    assert only_a[0].room == "a"


def test_search_zero_limit_short_circuits(adapter: StdioMemPalaceAdapter) -> None:
    """limit=0 returns [] without touching the subprocess."""
    adapter.write(content="whatever", wing="w", room="r")
    assert adapter.search("whatever", limit=0) == []


def test_search_empty_palace_returns_empty_list(
    adapter: StdioMemPalaceAdapter,
) -> None:
    """``{"error": "No palace found ..."}`` from ``mempalace_search`` → []."""
    # Drive the actual search() codepath, not just the raw _call_tool —
    # the fake routes wing="__no_palace__" to the benign error payload so
    # we exercise the ``error.startswith("No palace found")`` branch
    # end-to-end.
    hits = adapter.search("anything", wing="__no_palace__")
    assert hits == []


def test_search_unknown_error_raises_connect_error(
    adapter: StdioMemPalaceAdapter,
) -> None:
    """An *unknown* search error must NOT be silenced — bubbles to caller.

    Whitelisting only "No palace found" is intentional: ChromaDB blow-up,
    mis-typed args, etc. should surface as failures rather than be
    quietly converted to an empty result list (which would make ``ask``
    pretend the palace had no hits).
    """
    with pytest.raises(MemPalaceConnectError, match="ChromaDB exploded"):
        adapter.search("whatever", wing="__boom__")


# --------------------------------------------------------------- error paths


def test_tool_call_jsonrpc_error_raises_connect_error(
    adapter: StdioMemPalaceAdapter,
) -> None:
    """A JSON-RPC ``{"error": ...}`` reply bubbles up as MemPalaceConnectError."""
    with pytest.raises(MemPalaceConnectError, match="boom"):
        adapter._call_tool("mempalace_raise_error", {})  # noqa: SLF001 — test hook


def test_malformed_response_raises_connect_error() -> None:
    """A non-JSON response line surfaces as MemPalaceConnectError."""
    a = _spawn(mode="bad-json")
    try:
        with pytest.raises(MemPalaceConnectError, match="non-JSON"):
            a.write(content="anything", wing="w", room="r")
    finally:
        a.close()


def test_write_error_payload_raises_write_error() -> None:
    """A ``{"success": false}`` without a duplicate-match branch surfaces
    as :class:`MemPalaceWriteError` so the event ring records the failure."""
    # Use the "unknown tool" path by calling a tool name that the fake
    # refuses. That's a JSON-RPC-level error, handled above. For the
    # write-error-payload branch (success=false, reason!=duplicate) we
    # rely on the fact that no existing fake path produces it — instead
    # we drive it by monkeypatching the internal call. A cleaner way is
    # to extend the fake, but this keeps the unit test focused.
    a = _spawn()
    try:
        original = a._call_tool

        def fake_call(name: str, args: dict) -> dict:
            if name == "mempalace_add_drawer":
                return {"success": False, "reason": "rate-limited"}
            return original(name, args)

        a._call_tool = fake_call  # type: ignore[method-assign]  # test-only shim
        with pytest.raises(MemPalaceWriteError, match="rate-limited"):
            a.write(content="x", wing="w", room="r")
    finally:
        a.close()


def test_handshake_timeout_raises_and_cleans_up() -> None:
    """A slow ``initialize`` raises, and we don't leak the subprocess."""
    t0 = time.monotonic()
    with pytest.raises(MemPalaceConnectError, match="did not respond"):
        _spawn(mode="slow-handshake", sleep_s=3.0, handshake_timeout_s=0.5)
    elapsed = time.monotonic() - t0
    # Budget: 0.5s timeout + a generous shutdown allowance. Guards
    # against "forgot to raise; hung until the 3s sleep finished".
    assert elapsed < 2.5, f"handshake timeout took too long: {elapsed:.2f}s"


def test_call_timeout_raises_connect_error() -> None:
    """A slow tools/call past call_timeout_s surfaces as connect error."""
    a = _spawn(mode="slow-call", sleep_s=3.0, call_timeout_s=0.3)
    try:
        with pytest.raises(MemPalaceConnectError, match="did not respond"):
            a.write(content="x", wing="w", room="r")
    finally:
        a.close()


def test_subprocess_death_mid_call_raises_connect_error() -> None:
    """If the child exits during our call, we get MemPalaceConnectError."""
    a = _spawn(mode="die-on-first-call")
    try:
        with pytest.raises(MemPalaceConnectError):
            a.write(content="x", wing="w", room="r")
    finally:
        a.close()


# --------------------------------------------------------------- from_env


def test_from_env_respects_command_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ITSME_MEMPALACE_COMMAND is parsed into argv."""
    monkeypatch.setenv(
        "ITSME_MEMPALACE_COMMAND",
        " ".join(FAKE_CMD),
    )
    # Inherit FAKE_MP_MODE etc. from our own env.
    monkeypatch.setenv("FAKE_MP_MODE", "normal")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    a = StdioMemPalaceAdapter.from_env()
    try:
        assert a._command == list(FAKE_CMD)  # noqa: SLF001 — intentional probe
        # Verb check: exercise a real round-trip.
        result = a.write(content="via-env", wing="w", room="r")
        assert result.drawer_id
    finally:
        a.close()


def test_from_env_ignores_bad_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Garbage timeout env value doesn't crash boot — fallback + log."""
    monkeypatch.setenv(
        "ITSME_MEMPALACE_COMMAND",
        " ".join(FAKE_CMD),
    )
    monkeypatch.setenv("ITSME_MEMPALACE_HANDSHAKE_TIMEOUT", "not-a-number")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    with caplog.at_level("WARNING"):
        a = StdioMemPalaceAdapter.from_env()
        try:
            assert a.search("x") == []
        finally:
            a.close()
    assert any("ignoring bad" in r.message for r in caplog.records)
