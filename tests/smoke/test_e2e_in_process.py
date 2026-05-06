"""In-process end-to-end smoke (T1.20).

Validates the v0.0.1 capture/recall chain by wiring real components
together in one process — same shapes CC drives at runtime, minus the
stdio MCP framing and the bash shim layer.

Coverage matrix (✓ = exercised here):

================================  ================================  ===
Phase                              Component                          ✓
================================  ================================  ===
boot                               build_server registers 3 verbs    ✓
capture (explicit)                 Memory.remember sync fast-path    ✓
capture (lifecycle hook)           run_lifecycle_hook → bus           ✓
capture (pressure hook)            run_context_pressure (fire path)  ✓
capture (pressure hook, debounce)  fire then drop, re-arm, fire 2nd   ✓
recall (in-session)                Memory.ask after remember         ✓
recall (hook → router → ask)       v0.0.1 GA definition path         ✓
status                             Memory.status after activity      ✓
router idempotence                 dedup via memory.stored scan      ✓
hook disabled                      ITSME_HOOKS_DISABLED no-op         ✓
cross-MCP-restart drawer loss      v0.0.1 known gap (T1.13.5)         ✓
================================  ================================  ===

Things that are NOT here (deferred to ``test_subprocess.py``):

* The bash shims under ``hooks/cc/``
* ``python -m itsme.hooks`` argv parsing
* sqlite WAL behavior under real subprocess crash

Run via ``uv run pytest tests/smoke/ -v`` for the full smoke suite.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from itsme.core import Memory, build_default_memory
from itsme.core.adapters import InMemoryMemPalaceAdapter
from itsme.core.events import EventBus, EventType
from itsme.core.workers import WorkerScheduler
from itsme.hooks.context_pressure import run_context_pressure
from itsme.hooks.lifecycle import run_lifecycle_hook
from itsme.mcp.server import build_server

# ---------------------------------------------------------------- fixtures


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Per-test sqlite ring. Mirrors ``$ITSME_DB_PATH`` resolution."""
    return tmp_path / "events.db"


@pytest.fixture
def memory(db_path: Path) -> Iterator[Memory]:
    """Long-lived Memory bound to the test ring."""
    bus = EventBus(db_path=db_path, capacity=500)
    m = Memory(bus=bus, adapter=InMemoryMemPalaceAdapter(), project="smoke")
    yield m
    m.close()


def _hook_stdin(*, transcript_path: Path, session_id: str = "smoke-sid") -> str:
    """Construct a CC hook stdin payload pointing at *transcript_path*."""
    return json.dumps(
        {
            "transcript_path": str(transcript_path),
            "session_id": session_id,
            "hook_event_name": "TestHook",
            "cwd": str(transcript_path.parent),
        }
    )


def _write_transcript(path: Path, turns: list[str]) -> None:
    """Write a CC-shaped JSONL transcript with *turns* as user messages."""
    lines = []
    for i, text in enumerate(turns):
        lines.append(
            json.dumps(
                {
                    "type": "user" if i % 2 == 0 else "assistant",
                    "message": {"content": text},
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 1. boot


def test_boot_registers_three_verbs(memory: Memory) -> None:
    """The MCP layer surfaces exactly the 3 v0.0.1 verbs."""
    server = build_server(memory)
    # FastMCP exposes the registered handlers via ``_tool_manager._tools``
    # in mcp>=1.x. We probe through the public list_tools() path so the
    # test stays stable if the internal attr name changes.
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {"remember", "ask", "status"}, names


# ---------------------------------------------------------- 2. capture (explicit)


def test_remember_emits_full_event_chain(memory: Memory) -> None:
    """remember() → raw.captured + memory.routed + memory.stored, all linked."""
    res = memory.remember("smoke-explicit-fact", kind="fact")

    status = memory.status(scope="recent", limit=20)
    by_type: dict[str, list[str]] = {}
    for ev in status.events:
        by_type.setdefault(ev.type, []).append(ev.id)

    assert "raw.captured" in by_type
    assert "memory.routed" in by_type
    assert "memory.stored" in by_type
    # The result's stored_event_id must match a real memory.stored row.
    assert res.stored_event_id in by_type["memory.stored"]


# --------------------------------------------------- 3. capture (lifecycle hook)


def test_lifecycle_hook_emits_raw_captured(memory: Memory, tmp_path: Path) -> None:
    """before-exit hook → bus has raw.captured tagged hook:before-exit."""
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, ["I made a decision: pick Postgres."])

    out = run_lifecycle_hook(
        _hook_stdin(transcript_path=transcript),
        bus=memory._bus,
        source="hook:before-exit",
    )
    assert out["continue"] is True

    captured = memory._bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert len(captured) == 1
    env = captured[0]
    assert env.source == "hook:before-exit"
    assert "Postgres" in env.payload["content"]
    # transcript_ref must be retained so v0.0.2 can fetch the full file.
    assert env.payload["transcript_ref"] == {"path": str(transcript)}


# --------------------------------------------------- 4. capture (pressure hook)


def test_pressure_hook_fires_when_threshold_crossed(memory: Memory, tmp_path: Path) -> None:
    """A transcript that crosses 70% pressure produces one capture."""
    transcript = tmp_path / "transcript.jsonl"
    # max=10_000 tokens = 40_000 chars; 75% = 30_000 chars. Pad heavy.
    big_turn = "x " * 16_000  # ≈32_000 chars
    _write_transcript(transcript, [big_turn])

    state_dir = tmp_path / "state"
    out = run_context_pressure(
        _hook_stdin(transcript_path=transcript, session_id="pressure-sid-1"),
        bus=memory._bus,
        state_dir=state_dir,
        threshold=0.70,
        max_tokens=10_000,
    )
    assert out["continue"] is True
    # A fire emits a systemMessage so the operator sees the event in CC.
    assert "captured at" in out.get("systemMessage", "")

    captured = memory._bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    pressure_evts = [e for e in captured if e.source == "hook:context-pressure"]
    assert len(pressure_evts) == 1
    assert pressure_evts[0].payload["pressure"] >= 0.70


def test_pressure_hook_debounces_until_pressure_drops(memory: Memory, tmp_path: Path) -> None:
    """After firing, a slight dip must NOT re-fire until disarm_drop is met."""
    state_dir = tmp_path / "state"
    sid = "pressure-sid-debounce"

    # Tick 1: cross threshold, fire.
    big = tmp_path / "big.jsonl"
    _write_transcript(big, ["x " * 16_000])  # ≈80% of 10_000-token window
    run_context_pressure(
        _hook_stdin(transcript_path=big, session_id=sid),
        bus=memory._bus,
        state_dir=state_dir,
        threshold=0.70,
        max_tokens=10_000,
    )

    # Tick 2: shallow dip (still above threshold but below last_triggered).
    medium = tmp_path / "medium.jsonl"
    _write_transcript(medium, ["x " * 14_500])  # ≈72%
    run_context_pressure(
        _hook_stdin(transcript_path=medium, session_id=sid),
        bus=memory._bus,
        state_dir=state_dir,
        threshold=0.70,
        max_tokens=10_000,
    )

    pressure_evts = [
        e
        for e in memory._bus.tail(n=20, types=[EventType.RAW_CAPTURED])
        if e.source == "hook:context-pressure"
    ]
    assert len(pressure_evts) == 1, "second tick should be suppressed by Schmitt debounce"

    # Tick 3: deep relief — pressure must drop to ≤ last_triggered - disarm_drop.
    small = tmp_path / "small.jsonl"
    _write_transcript(small, ["x " * 4_000])  # ≈20%
    run_context_pressure(
        _hook_stdin(transcript_path=small, session_id=sid),
        bus=memory._bus,
        state_dir=state_dir,
        threshold=0.70,
        max_tokens=10_000,
    )
    # Tick 4: cross threshold again with re-armed state.
    run_context_pressure(
        _hook_stdin(transcript_path=big, session_id=sid),
        bus=memory._bus,
        state_dir=state_dir,
        threshold=0.70,
        max_tokens=10_000,
    )

    pressure_evts = [
        e
        for e in memory._bus.tail(n=20, types=[EventType.RAW_CAPTURED])
        if e.source == "hook:context-pressure"
    ]
    assert len(pressure_evts) == 2, "deep relief should re-arm and allow second fire"


# ------------------------------------------------------ 5. recall (in-session)


def test_remember_then_ask_finds_it(memory: Memory) -> None:
    """The in-process happy path: write then read from same Memory."""
    memory.remember("Postgres beat SQLite for concurrent worker pool", kind="decision")
    res = memory.ask("Postgres concurrent")
    assert res.sources, "ask should return at least one source"
    assert any("Postgres" in s.content for s in res.sources)


# ------------------------------------------ 6. recall (hook → router → ask)
# This is the exact v0.0.1 GA path: hook emits raw.captured, the router
# consume loop picks it up and writes to MemPalace, ask() reads it back.


def test_hook_capture_routes_then_is_askable(memory: Memory, tmp_path: Path) -> None:
    """v0.0.1 GA def — hook → router → MemPalace drawer → ask hits."""
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript,
        ["We decided on Tuesday: ship v0.0.1 with rule-based routing only."],
    )

    # Step 1: hook emits raw.captured (no scheduler running yet so we can
    # observe ordering).
    run_lifecycle_hook(
        _hook_stdin(transcript_path=transcript),
        bus=memory._bus,
        source="hook:before-exit",
    )

    # Step 2: spin the scheduler briefly so the consume loop drains the
    # one raw.captured into MemPalace.
    scheduler = WorkerScheduler()
    scheduler.add_worker(
        lambda: memory.consume_loop(ignore_sources=("explicit",), poll_interval=0.05)
    )
    scheduler.start()
    try:
        # Give the loop a tick or two to consume.
        _spin_until(
            lambda: any(
                e.source == "adapter:mempalace"
                for e in memory._bus.tail(n=20, types=[EventType.MEMORY_STORED])
            ),
            timeout_s=2.0,
        )
    finally:
        scheduler.stop()

    # Step 3: ask should now find the hook-captured content.
    res = memory.ask("ship v0.0.1")
    assert res.sources, "router should have written a drawer ask can find"
    assert any("rule-based" in s.content for s in res.sources)


# ------------------------------------------------------------- 7. status


def test_status_returns_recent_activity(memory: Memory) -> None:
    """status('recent') surfaces a recent remember()'s events."""
    memory.remember("status smoke test entry", kind="fact")
    res = memory.status(scope="recent", limit=10)
    assert res.count >= 2  # at least raw.captured + memory.stored
    types = {e.type for e in res.events}
    assert {"raw.captured", "memory.stored"}.issubset(types)


# --------------------------------------------------- 8. router idempotence


def test_consume_loop_skips_already_stored(memory: Memory, tmp_path: Path) -> None:
    """A second consume_loop pass must not re-write drawers."""
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, ["Idempotence test."])

    run_lifecycle_hook(
        _hook_stdin(transcript_path=transcript),
        bus=memory._bus,
        source="hook:before-exit",
    )

    scheduler = WorkerScheduler()
    scheduler.add_worker(
        lambda: memory.consume_loop(ignore_sources=("explicit",), poll_interval=0.05)
    )
    scheduler.start()
    try:
        _spin_until(
            lambda: any(
                e.source == "adapter:mempalace"
                for e in memory._bus.tail(n=20, types=[EventType.MEMORY_STORED])
            ),
            timeout_s=2.0,
        )
    finally:
        scheduler.stop()

    stored_after_first = len([e for e in memory._bus.tail(n=50, types=[EventType.MEMORY_STORED])])
    # Second pass: nothing new in raw.captured, so memory.stored count is
    # stable. We still spin a fresh scheduler to be sure.
    scheduler2 = WorkerScheduler()
    scheduler2.add_worker(
        lambda: memory.consume_loop(ignore_sources=("explicit",), poll_interval=0.05)
    )
    scheduler2.start()
    try:
        # Sleep long enough for at least one poll iteration but not enough
        # to do real work — _already_stored should bail it out fast.
        import time

        time.sleep(0.3)
    finally:
        scheduler2.stop()

    stored_after_second = len([e for e in memory._bus.tail(n=50, types=[EventType.MEMORY_STORED])])
    assert stored_after_second == stored_after_first


# --------------------------------------------------------- 9. hook disabled


def test_hook_noop_when_disabled(
    memory: Memory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ITSME_HOOKS_DISABLED=1 makes lifecycle hooks emit nothing."""
    monkeypatch.setenv("ITSME_HOOKS_DISABLED", "1")
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, ["should not be captured"])

    out = run_lifecycle_hook(
        _hook_stdin(transcript_path=transcript),
        bus=memory._bus,
        source="hook:before-exit",
    )
    assert out["continue"] is True
    captured = memory._bus.tail(n=10, types=[EventType.RAW_CAPTURED])
    assert captured == []


# -------------------------------------- 10. cross-MCP-restart drawer loss
# This documents a v0.0.1 known gap when MemPalace is *not* installed
# alongside itsme: ``InMemoryMemPalaceAdapter`` is RAM only, so when the
# MCP server restarts the drawers vanish — but the router's dedup key
# (``memory.stored`` events) lives in the persistent ring, so the new
# server skips the re-route. Net effect: drawer is silently lost across
# CC sessions.
#
# Originally this gap blocked persistence unconditionally because
# ``build_default_memory`` defaulted to ``inmemory``. After flipping the
# default to ``auto`` (this PR), the gap only manifests when MemPalace
# is missing from the environment — which is exactly the case in CI, so
# the test still asserts the lossy behavior. Once the test environment
# itself starts shipping with MemPalace (or once we mock the stdio
# adapter into ``build_default_memory`` here), the assertion will need
# to flip.


def test_cross_restart_drawer_loss_v001_known_gap(db_path: Path) -> None:
    """v0.0.1 known gap — drawer lost across MCP server restart when
    MemPalace isn't installed.

    Critically, both processes go through ``build_default_memory`` (the
    same factory the MCP server uses). With the post-T1.13.5 default of
    ``ITSME_MEMPALACE_BACKEND=auto``, this lands on the inmemory
    fallback when MemPalace isn't on PATH (the CI condition). Once test
    fixtures provide MemPalace, this test will fail and the assertion
    flips to ``len(res.sources) == 1``.
    """
    # Session 1: write through process A.
    mem_a = build_default_memory(project="restart", db_path=db_path)
    mem_a.remember("alpha-restart-token", kind="fact")
    # Process A would normally close MemPalace here. Drawers go away.
    mem_a.close()

    # Session 2: a fresh process opens the same events ring via the
    # production factory. With ``ITSME_MEMPALACE_BACKEND=auto`` (the
    # post-T1.13.5 default), this lands on the inmemory fallback when
    # MemPalace isn't on PATH — exactly the CI condition. The router's
    # dedup keys off the persistent ring, so it sees ``memory.stored``
    # already → skips the ``raw.captured`` → adapter B stays empty.
    # Once the test environment provides MemPalace (so the ``auto``
    # path resolves to the persistent stdio backend), the same code
    # path will return the drawer and the assertion below will need to
    # flip to ``len(res.sources) == 1``.
    mem_b = build_default_memory(project="restart", db_path=db_path)

    scheduler = WorkerScheduler()
    scheduler.add_worker(
        lambda: mem_b.consume_loop(ignore_sources=("explicit",), poll_interval=0.05)
    )
    scheduler.start()
    try:
        import time

        time.sleep(0.3)  # one poll cycle is enough
    finally:
        scheduler.stop()

    # The smoke fact: ask in process B finds nothing.
    res = mem_b.ask("alpha-restart-token")
    mem_b.close()
    assert res.sources == [], (
        "Cross-restart drawer survival landed earlier than expected — "
        "T1.13.5 (persistent MemPalace adapter) likely shipped. Flip the "
        "assertion to ``len(res.sources) == 1`` and update the docstring."
    )


# ------------------------------------------------------------- helpers


def _spin_until(predicate, *, timeout_s: float = 2.0, interval_s: float = 0.05) -> None:
    """Poll *predicate* until True or timeout. Used to wait on async workers."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_s)
    raise AssertionError(f"predicate never became true within {timeout_s}s")


# ----------------------------------------- 11. build_default_memory smoke


def test_build_default_memory_round_trip(tmp_path: Path) -> None:
    """``build_default_memory`` (the path MCP server uses) supports remember/ask."""
    db = tmp_path / "default.db"
    mem = build_default_memory(project="bdm-smoke", db_path=db)
    try:
        mem.remember("default-memory roundtrip token", kind="fact")
        res = mem.ask("roundtrip token")
        assert res.sources
    finally:
        mem.close()
