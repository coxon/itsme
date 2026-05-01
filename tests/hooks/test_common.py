"""Tests for shared hook utilities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from itsme.hooks import _common


def test_load_hook_input_accepts_valid_json() -> None:
    out = _common.load_hook_input('{"session_id": "s1", "hook_event_name": "Stop"}')
    assert out["session_id"] == "s1"


def test_load_hook_input_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        _common.load_hook_input("")
    with pytest.raises(ValueError, match="empty"):
        _common.load_hook_input("   \n")


def test_load_hook_input_rejects_bad_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        _common.load_hook_input("not json")


def test_load_hook_input_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        _common.load_hook_input('["a", "b"]')


def test_hooks_disabled_env_truthy() -> None:
    for v in ("1", "true", "yes", "TRUE", "Yes"):
        with patch.dict(os.environ, {"ITSME_HOOKS_DISABLED": v}):
            assert _common.hooks_disabled() is True


def test_hooks_disabled_env_falsy() -> None:
    for v in ("", "0", "false", "no", "off", "garbage"):
        with patch.dict(os.environ, {"ITSME_HOOKS_DISABLED": v}):
            assert _common.hooks_disabled() is False


def test_resolve_db_path_honors_env(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"ITSME_DB_PATH": str(tmp_path / "x.db")}):
        assert _common.resolve_db_path() == tmp_path / "x.db"


def test_resolve_db_path_expands_user() -> None:
    with patch.dict(os.environ, {"ITSME_DB_PATH": "~/itsme-x.db"}):
        p = _common.resolve_db_path()
        assert "~" not in str(p)
        assert p.name == "itsme-x.db"


def test_resolve_db_path_falls_back_to_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ITSME_DB_PATH", None)
        p = _common.resolve_db_path()
        assert p.name == "events.db"
        assert ".itsme" in p.parts


def test_resolve_project_defaults() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ITSME_PROJECT", None)
        assert _common.resolve_project() == "default"


def test_resolve_project_env_override() -> None:
    with patch.dict(os.environ, {"ITSME_PROJECT": "itsme-dev"}):
        assert _common.resolve_project() == "itsme-dev"


def test_resolve_state_dir_honors_env(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"ITSME_STATE_DIR": str(tmp_path)}):
        assert _common.resolve_state_dir() == tmp_path


def test_resolve_state_dir_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ITSME_STATE_DIR", None)
        p = _common.resolve_state_dir()
        assert p.name == "state"
        assert ".itsme" in p.parts


def _write_transcript(path: Path, messages: list[str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps({"type": "user", "message": {"content": m}}) + "\n")


def test_read_transcript_tail_basic(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_transcript(p, ["one", "two", "three"])
    out = _common.read_transcript_tail(p, max_chars=100)
    assert "one\ntwo\nthree" in out


def test_read_transcript_tail_caps_length(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_transcript(p, ["x" * 1000, "y" * 1000])
    out = _common.read_transcript_tail(p, max_chars=500)
    assert len(out) <= 500
    # Newest content should be in the tail.
    assert out.endswith("y" * min(500, 1000)) or "y" in out


def test_read_transcript_tail_missing_file(tmp_path: Path) -> None:
    assert _common.read_transcript_tail(tmp_path / "missing", max_chars=100) == ""


def test_read_transcript_tail_zero_max_chars(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_transcript(p, ["anything"])
    assert _common.read_transcript_tail(p, max_chars=0) == ""


def test_read_transcript_handles_list_content(tmp_path: Path) -> None:
    """Content blocks (text + tool_use) should yield only the text parts."""
    p = tmp_path / "t.jsonl"
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "thinking..."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                {"type": "text", "text": "done"},
            ]
        },
    }
    p.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    out = _common.read_transcript_tail(p, max_chars=1000)
    assert "thinking..." in out
    assert "done" in out
    assert "tool_use" not in out


def test_read_transcript_skips_malformed_lines(tmp_path: Path) -> None:
    """Partial JSONL lines (e.g. from an abrupt session end) are ignored."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"content": "good"}}) + "\n"
        "{broken partial line\n"
        + json.dumps({"type": "user", "message": {"content": "also good"}})
        + "\n",
        encoding="utf-8",
    )
    out = _common.read_transcript_tail(p, max_chars=1000)
    assert "good" in out
    assert "also good" in out
    assert "broken" not in out


def test_estimate_tokens() -> None:
    assert _common.estimate_tokens("") == 0
    assert _common.estimate_tokens("abcd") == 1
    assert _common.estimate_tokens("a" * 400) == 100


def test_ok_output_shape() -> None:
    out = _common.ok_output()
    assert out == {"continue": True, "suppressOutput": True}


def test_ok_output_with_system_message() -> None:
    out = _common.ok_output(system_message="hi")
    assert out["systemMessage"] == "hi"
