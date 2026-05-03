"""Subprocess smoke (T1.20).

Drives the bash shims under ``hooks/cc/`` via ``subprocess.run`` to
validate the actual install path:

* The shim invokes ``python -m itsme.hooks <name>``
* ``ITSME_DB_PATH``, ``ITSME_PROJECT``, ``ITSME_STATE_DIR`` are honored
* The hook returns a CC-shaped JSON output on stdout, exits 0
* The events sqlite ring grows with the expected ``raw.captured`` rows

These tests are the most expensive in the suite (each spawns a fresh
Python interpreter) but they catch failures the in-process tests
can't — packaging, argv parsing, sqlite WAL across processes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks" / "cc"


# ---------------------------------------------------------------- helpers


def _hook_env(*, db_path: Path, state_dir: Path) -> dict[str, str]:
    """Build a minimal env that points hooks at test paths.

    Inherits PATH / HOME from the parent so ``bash`` and ``python`` are
    findable; the rest is pruned to keep tests deterministic.
    """
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "ITSME_DB_PATH": str(db_path),
        "ITSME_PROJECT": "subprocess-smoke",
        "ITSME_STATE_DIR": str(state_dir),
        # Make sure the venv's python is on PYTHONPATH so the shim can
        # import itsme. ``CLAUDE_PLUGIN_ROOT`` mimics how CC sets it.
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
    }
    # Pass through anything starting with PYTHON / VIRTUAL — uv's runner
    # uses these to locate the right interpreter.
    for k, v in os.environ.items():
        if k.startswith(("PYTHON", "VIRTUAL")) or k in {"UV_PROJECT_ENVIRONMENT"}:
            env[k] = v
    return env


def _run_shim(name: str, *, stdin: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke ``hooks/cc/<name>.sh`` with *stdin* piped in."""
    shim = HOOKS_DIR / f"{name}.sh"
    assert shim.exists(), f"missing shim {shim}"
    return subprocess.run(
        ["bash", str(shim)],
        input=stdin,
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _hook_stdin(*, transcript: Path, session_id: str = "subproc-sid") -> str:
    """A CC-shaped hook payload."""
    return json.dumps(
        {
            "transcript_path": str(transcript),
            "session_id": session_id,
            "hook_event_name": "TestHook",
            "cwd": str(transcript.parent),
        }
    )


def _write_transcript(path: Path, turns: list[str]) -> None:
    """Write a CC JSONL transcript with *turns* as user messages."""
    lines = [
        json.dumps(
            {
                "type": "user" if i % 2 == 0 else "assistant",
                "message": {"content": text},
            }
        )
        for i, text in enumerate(turns)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ring_event_count(db_path: Path) -> dict[str, int]:
    """Count events by type via a fresh EventBus pointed at *db_path*.

    We open + close on each call so we don't keep a writer handle that
    would block subsequent shim invocations on platforms with stricter
    sqlite locking.
    """
    from itsme.core.events import EventBus

    bus = EventBus(db_path=db_path, capacity=500)
    try:
        events = bus.tail(n=500)
        counts: dict[str, int] = {}
        for e in events:
            counts[e.type.value] = counts.get(e.type.value, 0) + 1
        return counts
    finally:
        bus.close()


# ------------------------------------------------------------ fixtures


@pytest.fixture
def shim_env(tmp_path: Path) -> dict[str, str]:
    """Per-test env dict with isolated db + state dir."""
    return _hook_env(
        db_path=tmp_path / "events.db",
        state_dir=tmp_path / "state",
    )


# ----------------------------------------------------------- tests


@pytest.mark.skipif(sys.platform == "win32", reason="bash shims, POSIX-only")
def test_before_exit_shim_writes_raw_captured(tmp_path: Path, shim_env: dict[str, str]) -> None:
    """before-exit.sh + a CC-shaped stdin → events DB gains raw.captured."""
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, ["Subprocess smoke: decided on Postgres."])

    proc = _run_shim(
        "before-exit",
        stdin=_hook_stdin(transcript=transcript),
        env=shim_env,
    )
    assert proc.returncode == 0, f"shim failed: stderr={proc.stderr}"
    out = json.loads(proc.stdout)
    assert out.get("continue") is True

    # Both count + source: a typo'd source label or fallback to a
    # different producer string would still grow the count by 1, so the
    # source assertion is what actually proves the right shim ran.
    from itsme.core.events import EventBus, EventType

    bus = EventBus(db_path=Path(shim_env["ITSME_DB_PATH"]), capacity=500)
    try:
        captured = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
        assert len(captured) == 1
        assert captured[0].source == "hook:before-exit"
    finally:
        bus.close()


@pytest.mark.skipif(sys.platform == "win32", reason="bash shims, POSIX-only")
def test_before_compact_shim_writes_raw_captured(tmp_path: Path, shim_env: dict[str, str]) -> None:
    """before-compact.sh path is identical to before-exit but with a
    different source label."""
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, ["About to compact: keep this slice."])

    proc = _run_shim(
        "before-compact",
        stdin=_hook_stdin(transcript=transcript),
        env=shim_env,
    )
    assert proc.returncode == 0, proc.stderr

    from itsme.core.events import EventBus, EventType

    bus = EventBus(db_path=Path(shim_env["ITSME_DB_PATH"]), capacity=500)
    try:
        captured = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
        assert len(captured) == 1
        assert captured[0].source == "hook:before-compact"
    finally:
        bus.close()


@pytest.mark.skipif(sys.platform == "win32", reason="bash shims, POSIX-only")
def test_context_pressure_shim_fires_when_transcript_is_full(
    tmp_path: Path, shim_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """context-pressure.sh with a >70% transcript → raw.captured row."""
    transcript = tmp_path / "transcript.jsonl"
    # Keep token estimate small enough to actually cross threshold without
    # ballooning the test fixture: override max_tokens via env.
    big_turn = "x " * 16_000  # ≈32_000 chars ≈8000 tokens
    _write_transcript(transcript, [big_turn])

    shim_env["ITSME_CTX_MAX"] = "10000"
    shim_env["ITSME_CTX_THRESHOLD"] = "0.70"

    proc = _run_shim(
        "context-pressure",
        stdin=_hook_stdin(transcript=transcript, session_id="subproc-pressure-sid"),
        env=shim_env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out.get("continue") is True
    # Fire path emits a systemMessage; absence means the hook didn't
    # cross threshold (test fixture bug).
    assert "captured at" in out.get("systemMessage", ""), out

    from itsme.core.events import EventBus, EventType

    bus = EventBus(db_path=Path(shim_env["ITSME_DB_PATH"]), capacity=500)
    try:
        captured = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
        sources = [e.source for e in captured]
        assert sources == ["hook:context-pressure"], sources
    finally:
        bus.close()


@pytest.mark.skipif(sys.platform == "win32", reason="bash shims, POSIX-only")
def test_shim_respects_hooks_disabled(tmp_path: Path, shim_env: dict[str, str]) -> None:
    """ITSME_HOOKS_DISABLED=1 → shim exits 0 with continue:true and no
    rows added."""
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, ["should not be captured"])
    shim_env["ITSME_HOOKS_DISABLED"] = "1"

    proc = _run_shim(
        "before-exit",
        stdin=_hook_stdin(transcript=transcript),
        env=shim_env,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out.get("continue") is True

    counts = _ring_event_count(Path(shim_env["ITSME_DB_PATH"]))
    # The disabled fast path doesn't even open the bus; the file may not
    # exist yet. Either is acceptable.
    assert counts.get("raw.captured", 0) == 0, counts


@pytest.mark.skipif(sys.platform == "win32", reason="bash shims, POSIX-only")
def test_shim_handles_empty_transcript_gracefully(tmp_path: Path, shim_env: dict[str, str]) -> None:
    """A missing/empty transcript file makes the hook a no-op (exit 0)."""
    nowhere = tmp_path / "does-not-exist.jsonl"
    proc = _run_shim(
        "before-exit",
        stdin=_hook_stdin(transcript=nowhere),
        env=shim_env,
    )
    # Even with no transcript text, the shim must exit 0 — CC treats
    # non-zero as a UI error and that's wrong for passive capture.
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out.get("continue") is True

    counts = _ring_event_count(Path(shim_env["ITSME_DB_PATH"]))
    assert counts.get("raw.captured", 0) == 0
