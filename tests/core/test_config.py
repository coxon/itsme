"""Tests for core.config — T4.13 centralised configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from itsme.core.config import (
    DEFAULT_CTX_THRESHOLD,
    DEFAULT_DEDUP_THRESHOLD,
    DEFAULT_LLM_MODEL,
    DEFAULT_MEMPALACE_BACKEND,
    DEFAULT_PROJECT,
    _flatten_toml,
    _parse_bool,
    _read_env,
    load_config,
)

# -------------------------------------------------------- defaults


class TestDefaults:
    def test_default_config_has_expected_values(self) -> None:
        cfg = load_config(skip_file=True, skip_env=True)
        assert cfg.project == DEFAULT_PROJECT
        assert cfg.dedup_threshold == DEFAULT_DEDUP_THRESHOLD
        assert cfg.llm_model == DEFAULT_LLM_MODEL
        assert cfg.mempalace_backend == DEFAULT_MEMPALACE_BACKEND
        assert cfg.ctx_threshold == DEFAULT_CTX_THRESHOLD
        assert cfg.hooks_disabled is False

    def test_config_is_frozen(self) -> None:
        cfg = load_config(skip_file=True, skip_env=True)
        with pytest.raises(AttributeError):
            cfg.project = "changed"  # type: ignore[misc]


# -------------------------------------------------------- env vars


class TestEnvOverrides:
    def test_env_overrides_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_PROJECT", "myproject")
        cfg = load_config(skip_file=True)
        assert cfg.project == "myproject"

    def test_env_overrides_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_DEDUP_THRESHOLD", "0.90")
        cfg = load_config(skip_file=True)
        assert cfg.dedup_threshold == 0.90

    def test_env_overrides_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_CTX_MAX", "150000")
        cfg = load_config(skip_file=True)
        assert cfg.ctx_max_tokens == 150000

    def test_env_overrides_bool_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("1", "true", "True", "yes", "on"):
            monkeypatch.setenv("ITSME_HOOKS_DISABLED", val)
            cfg = load_config(skip_file=True)
            assert cfg.hooks_disabled is True

    def test_env_overrides_bool_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("ITSME_HOOKS_DISABLED", val)
            cfg = load_config(skip_file=True)
            assert cfg.hooks_disabled is False

    def test_env_overrides_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        cfg = load_config(skip_file=True)
        assert cfg.llm_api_key == "sk-test-key"

    def test_invalid_float_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_DEDUP_THRESHOLD", "not-a-number")
        cfg = load_config(skip_file=True)
        assert cfg.dedup_threshold == DEFAULT_DEDUP_THRESHOLD  # fallback to default

    def test_legacy_aleph_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_ALEPH_VAULT", "/old/path")
        monkeypatch.delenv("ITSME_ALEPH_ROOT", raising=False)
        cfg = load_config(skip_file=True)
        assert cfg.aleph_root == "/old/path"

    def test_aleph_root_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_ALEPH_ROOT", "/new/path")
        monkeypatch.setenv("ITSME_ALEPH_VAULT", "/old/path")
        cfg = load_config(skip_file=True)
        assert cfg.aleph_root == "/new/path"

    def test_db_path_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITSME_DB_PATH", "/tmp/test.db")
        cfg = load_config(skip_file=True)
        assert cfg.db_path == "/tmp/test.db"


# -------------------------------------------------------- TOML file


class TestTomlFile:
    def test_toml_overrides(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[storage]
project = "from-toml"

[thresholds]
dedup = 0.92

[llm]
model = "gpt-4o"
"""
        )
        cfg = load_config(config_path=config_file, skip_env=True)
        assert cfg.project == "from-toml"
        assert cfg.dedup_threshold == 0.92
        assert cfg.llm_model == "gpt-4o"

    def test_env_beats_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[storage]
project = "from-toml"
"""
        )
        monkeypatch.setenv("ITSME_PROJECT", "from-env")
        cfg = load_config(config_path=config_file)
        assert cfg.project == "from-env"

    def test_missing_toml_uses_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(config_path=tmp_path / "nonexistent.toml", skip_env=True)
        assert cfg.project == DEFAULT_PROJECT

    def test_invalid_toml_uses_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / "bad.toml"
        config_file.write_text("this is not valid toml {{{{")
        cfg = load_config(config_path=config_file, skip_env=True)
        assert cfg.project == DEFAULT_PROJECT

    def test_unknown_toml_keys_ignored(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[storage]
project = "ok"
unknown_key = "ignored"

[unknown_section]
foo = "bar"
"""
        )
        cfg = load_config(config_path=config_file, skip_env=True)
        assert cfg.project == "ok"

    def test_records_config_file_path(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[storage]\nproject = "test"\n')
        cfg = load_config(config_path=config_file, skip_env=True)
        assert cfg._config_file == str(config_file)

    def test_all_toml_sections(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[storage]
db_path = "/tmp/events.db"
project = "proj"
mempalace_backend = "inmemory"
mempalace_command = "custom-cmd"
mempalace_handshake_timeout = 5.0
mempalace_call_timeout = 15.0

[aleph]
root = "/my/wiki"

[llm]
model = "claude-4"
base_url = "https://api.example.com"
max_tokens = 4096
api_key = "sk-toml"

[thresholds]
dedup = 0.80

[hooks]
disabled = true
ctx_threshold = 0.60
ctx_max_tokens = 100000
snapshot_chars = 5000
state_dir = "/tmp/state"
"""
        )
        cfg = load_config(config_path=config_file, skip_env=True)
        assert cfg.db_path == "/tmp/events.db"
        assert cfg.project == "proj"
        assert cfg.mempalace_backend == "inmemory"
        assert cfg.mempalace_command == "custom-cmd"
        assert cfg.mempalace_handshake_timeout == 5.0
        assert cfg.mempalace_call_timeout == 15.0
        assert cfg.aleph_root == "/my/wiki"
        assert cfg.llm_model == "claude-4"
        assert cfg.llm_base_url == "https://api.example.com"
        assert cfg.llm_max_tokens == 4096
        assert cfg.llm_api_key == "sk-toml"
        assert cfg.dedup_threshold == 0.80
        assert cfg.hooks_disabled is True
        assert cfg.ctx_threshold == 0.60
        assert cfg.ctx_max_tokens == 100000
        assert cfg.snapshot_chars == 5000
        assert cfg.state_dir == "/tmp/state"


# -------------------------------------------------------- helpers


class TestHelpers:
    def test_parse_bool(self) -> None:
        assert _parse_bool("1") is True
        assert _parse_bool("true") is True
        assert _parse_bool("True") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True
        assert _parse_bool("0") is False
        assert _parse_bool("false") is False
        assert _parse_bool("no") is False
        assert _parse_bool("") is False

    def test_flatten_toml_ignores_non_dict_sections(self) -> None:
        result = _flatten_toml({"top_level_key": "value"})
        assert result == {}

    def test_read_env_empty(self) -> None:
        # Just ensure it returns a dict (may or may not be empty depending on actual env)
        result = _read_env()
        assert isinstance(result, dict)
        # Just ensure it returns a dict (may or may not be empty depending on actual env)
        result = _read_env()
        assert isinstance(result, dict)
