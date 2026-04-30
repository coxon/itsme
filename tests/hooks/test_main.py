"""Tests for the ``python -m itsme.hooks`` CLI dispatcher."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from itsme.core.events import EventBus, EventType
from itsme.hooks.__main__ import main


@pytest.fixture
def isolated_db(tmp_path: Path) -> Path:
    """Point the hook process at a throwaway ring file."""
    db = tmp_path / "events.db"
    return db


def _make_transcript(path: Path, messages: list[str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps({"type": "user", "message": {"content": m}}) + "\n")


def _run_hook(
    name: str,
    *,
    stdin: str,
    db_path: Path,
    state_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Invoke the hook CLI with isolated env + injected stdin."""
    env_patch = {
        "ITSME_DB_PATH": str(db_path),
        "ITSME_STATE_DIR": str(state_dir),  # not yet read by code; future-proof
        "ITSME_HOOKS_DISABLED": "",
    }
    env_patch.update(extra_env or {})

    with patch.dict(os.environ, env_patch):
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(stdin)
        sys.stdout = io.StringIO()
        try:
            return main([name])
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout


def test_unknown_hook_name_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Typo'd hook name is a developer bug, not a runtime event."""
    rc = main(["bogus-hook"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown hook" in err


def test_no_argv_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_too_many_args_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["before-exit", "extra"])
    assert rc == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_before_exit_writes_event(tmp_path: Path, isolated_db: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["hello", "world"])
    stdin = json.dumps(
        {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": "/tmp",
            "hook_event_name": "SessionEnd",
        }
    )

    rc = _run_hook(
        "before-exit",
        stdin=stdin,
        db_path=isolated_db,
        state_dir=tmp_path / "state",
    )
    assert rc == 0

    bus = EventBus(db_path=isolated_db)
    try:
        events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
        assert len(events) == 1
        assert events[0].source == "hook:before-exit"
    finally:
        bus.close()


def test_bad_stdin_exits_zero_logs_stderr(
    isolated_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed stdin must not surface as a CC UI error (rc=0)."""
    rc = _run_hook(
        "before-exit",
        stdin="not json at all",
        db_path=isolated_db,
        state_dir=tmp_path / "state",
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "bad input" in err


def test_disabled_env_short_circuits(tmp_path: Path, isolated_db: Path) -> None:
    """``ITSME_HOOKS_DISABLED=1`` ⇒ rc=0, no event."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["should not be saved"])
    stdin = json.dumps(
        {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": "/tmp",
            "hook_event_name": "SessionEnd",
        }
    )

    rc = _run_hook(
        "before-exit",
        stdin=stdin,
        db_path=isolated_db,
        state_dir=tmp_path / "state",
        extra_env={"ITSME_HOOKS_DISABLED": "1"},
    )
    assert rc == 0

    bus = EventBus(db_path=isolated_db)
    try:
        assert bus.count() == 0
    finally:
        bus.close()


def test_context_pressure_routes_to_handler(tmp_path: Path, isolated_db: Path) -> None:
    """Smoke test that the dispatcher hands context-pressure to the right module."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, ["x" * 4000])  # 1000 tokens
    stdin = json.dumps(
        {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": "/tmp",
            "hook_event_name": "UserPromptSubmit",
        }
    )

    rc = _run_hook(
        "context-pressure",
        stdin=stdin,
        db_path=isolated_db,
        state_dir=tmp_path / "state",
        extra_env={"ITSME_CTX_THRESHOLD": "0.05", "ITSME_CTX_MAX": "10000"},
    )
    assert rc == 0

    bus = EventBus(db_path=isolated_db)
    try:
        events = bus.tail(n=10, types=[EventType.RAW_CAPTURED])
        assert len(events) == 1
        assert events[0].source == "hook:context-pressure"
    finally:
        bus.close()


def test_disabled_env_short_circuits_before_bus_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With HOOKS_DISABLED=1, an unwritable DB path must NOT cause stderr noise.

    Regression for CodeRabbit PR#7 r1: previously ``open_bus()`` ran
    before the disable check, so a disabled user with a bad db path
    still saw "failed to open events ring" on every hook tick.
    """
    # Make the parent unwritable so any attempt to open would fail.
    unwritable_parent = tmp_path / "locked"
    unwritable_parent.mkdir()
    unwritable_parent.chmod(0o000)
    try:
        stdin = json.dumps(
            {
                "session_id": "sess-1",
                "transcript_path": str(tmp_path / "missing.jsonl"),
                "cwd": "/tmp",
                "hook_event_name": "SessionEnd",
            }
        )
        rc = _run_hook(
            "before-exit",
            stdin=stdin,
            db_path=unwritable_parent / "blocked.db",
            state_dir=tmp_path / "state",
            extra_env={"ITSME_HOOKS_DISABLED": "1"},
        )
        assert rc == 0
        # No "failed to open events ring" spam.
        err = capsys.readouterr().err
        assert "failed to open events ring" not in err
    finally:
        unwritable_parent.chmod(0o755)
