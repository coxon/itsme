"""Centralised configuration — T4.13.

Single source of truth for all tuneable settings. Resolution order
(highest priority first):

1. **Environment variable** — e.g. ``$ITSME_DEDUP_THRESHOLD``
2. **Config file** — ``~/.itsme/config.toml`` (optional, never required)
3. **Built-in default** — hardcoded in this module

The ``Config`` dataclass is frozen after construction: every consumer
reads a snapshot, no runtime mutation. To "reload", construct a new
``Config`` (which re-reads env + file).

Usage::

    from itsme.core.config import load_config
    cfg = load_config()          # reads env + file + defaults
    cfg.dedup_threshold          # 0.85 (or whatever was configured)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- defaults

#: Default config file location.
DEFAULT_CONFIG_PATH = Path.home() / ".itsme" / "config.toml"

# -- storage
DEFAULT_DB_PATH: str = str(Path.home() / ".itsme" / "events.db")
DEFAULT_PROJECT: str = "default"
DEFAULT_MEMPALACE_BACKEND: str = "auto"
DEFAULT_MEMPALACE_COMMAND: str = "python3 -m mempalace.mcp_server"
DEFAULT_MEMPALACE_HANDSHAKE_TIMEOUT: float = 10.0
DEFAULT_MEMPALACE_CALL_TIMEOUT: float = 30.0

# -- aleph / wiki
DEFAULT_ALEPH_ROOT: str = ""  # empty = auto-discover

# -- llm
DEFAULT_LLM_MODEL: str = "deepseek-chat"
DEFAULT_LLM_BASE_URL: str = "https://api.deepseek.com"
DEFAULT_LLM_MAX_TOKENS: int = 2048

# -- thresholds
DEFAULT_DEDUP_THRESHOLD: float = 0.85

# -- context pressure hook
DEFAULT_CTX_THRESHOLD: float = 0.70
DEFAULT_CTX_MAX_TOKENS: int = 200_000

# -- hooks
DEFAULT_HOOKS_DISABLED: bool = False
DEFAULT_SNAPSHOT_CHARS: int = 10_000


# ---------------------------------------------------------------- dataclass


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of all itsme settings.

    Constructed by :func:`load_config` which merges env → file → defaults.
    Fields match the ``ITSME_*`` env var names (lowercased, prefix stripped).
    """

    # -- storage
    db_path: str = DEFAULT_DB_PATH
    project: str = DEFAULT_PROJECT
    mempalace_backend: str = DEFAULT_MEMPALACE_BACKEND
    mempalace_command: str = DEFAULT_MEMPALACE_COMMAND
    mempalace_handshake_timeout: float = DEFAULT_MEMPALACE_HANDSHAKE_TIMEOUT
    mempalace_call_timeout: float = DEFAULT_MEMPALACE_CALL_TIMEOUT

    # -- aleph / wiki
    aleph_root: str = DEFAULT_ALEPH_ROOT

    # -- llm
    llm_model: str = DEFAULT_LLM_MODEL
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    llm_api_key: str = ""  # from $DEEPSEEK_API_KEY

    # -- thresholds
    dedup_threshold: float = DEFAULT_DEDUP_THRESHOLD

    # -- context pressure
    ctx_threshold: float = DEFAULT_CTX_THRESHOLD
    ctx_max_tokens: int = DEFAULT_CTX_MAX_TOKENS

    # -- hooks
    hooks_disabled: bool = DEFAULT_HOOKS_DISABLED
    snapshot_chars: int = DEFAULT_SNAPSHOT_CHARS
    state_dir: str = ""  # empty = ~/.itsme/state/

    # -- config file path (informational)
    _config_file: str = field(default="", repr=False)


# ---------------------------------------------------------------- env mapping

#: Maps Config field name → (env var name, type converter).
#: Order doesn't matter — we iterate all of them.
_ENV_MAP: dict[str, tuple[str, type]] = {
    "db_path": ("ITSME_DB_PATH", str),
    "project": ("ITSME_PROJECT", str),
    "mempalace_backend": ("ITSME_MEMPALACE_BACKEND", str),
    "mempalace_command": ("ITSME_MEMPALACE_COMMAND", str),
    "mempalace_handshake_timeout": ("ITSME_MEMPALACE_HANDSHAKE_TIMEOUT", float),
    "mempalace_call_timeout": ("ITSME_MEMPALACE_CALL_TIMEOUT", float),
    "aleph_root": ("ITSME_ALEPH_ROOT", str),
    "llm_model": ("ITSME_LLM_MODEL", str),
    "llm_base_url": ("ITSME_LLM_BASE_URL", str),
    "llm_api_key": ("DEEPSEEK_API_KEY", str),
    "dedup_threshold": ("ITSME_DEDUP_THRESHOLD", float),
    "ctx_threshold": ("ITSME_CTX_THRESHOLD", float),
    "ctx_max_tokens": ("ITSME_CTX_MAX", int),
    "hooks_disabled": ("ITSME_HOOKS_DISABLED", bool),
    "snapshot_chars": ("ITSME_SNAPSHOT_CHARS", int),
    "state_dir": ("ITSME_STATE_DIR", str),
}


def _parse_bool(raw: str) -> bool:
    """Parse a boolean from an env var string."""
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_env() -> dict[str, Any]:
    """Read all known ITSME_* env vars, return overrides dict."""
    overrides: dict[str, Any] = {}
    for field_name, (env_name, typ) in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            continue
        try:
            if typ is bool:
                overrides[field_name] = _parse_bool(raw)
            else:
                overrides[field_name] = typ(raw)
        except (ValueError, TypeError) as exc:
            _logger.warning(
                "itsme config: ignoring invalid %s=%r: %s",
                env_name,
                raw,
                exc,
            )
    # Legacy: $ITSME_ALEPH_VAULT → aleph_root (if ITSME_ALEPH_ROOT not set)
    if "aleph_root" not in overrides:
        legacy = os.environ.get("ITSME_ALEPH_VAULT", "").strip()
        if legacy:
            overrides["aleph_root"] = legacy
    return overrides


# ---------------------------------------------------------------- toml file


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML config file, return flat overrides dict.

    The TOML file uses sections that map to field prefixes::

        [storage]
        db_path = "~/.itsme/events.db"
        project = "myproject"

        [llm]
        model = "deepseek-chat"
        api_key = "sk-..."

        [thresholds]
        dedup = 0.85

    Returns an empty dict if the file doesn't exist or can't be parsed.
    """
    if not path.is_file():
        return {}

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            _logger.debug("itsme config: no TOML parser available, skipping config file")
            return {}

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        _logger.warning("itsme config: failed to parse %s: %s", path, exc)
        return {}

    return _flatten_toml(data)


#: Maps TOML section.key → Config field name.
_TOML_MAP: dict[str, str] = {
    # [storage]
    "storage.db_path": "db_path",
    "storage.project": "project",
    "storage.mempalace_backend": "mempalace_backend",
    "storage.mempalace_command": "mempalace_command",
    "storage.mempalace_handshake_timeout": "mempalace_handshake_timeout",
    "storage.mempalace_call_timeout": "mempalace_call_timeout",
    # [aleph]
    "aleph.root": "aleph_root",
    # [llm]
    "llm.model": "llm_model",
    "llm.base_url": "llm_base_url",
    "llm.max_tokens": "llm_max_tokens",
    "llm.api_key": "llm_api_key",
    # [thresholds]
    "thresholds.dedup": "dedup_threshold",
    # [hooks]
    "hooks.disabled": "hooks_disabled",
    "hooks.ctx_threshold": "ctx_threshold",
    "hooks.ctx_max_tokens": "ctx_max_tokens",
    "hooks.snapshot_chars": "snapshot_chars",
    "hooks.state_dir": "state_dir",
}


def _flatten_toml(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested TOML sections to Config field names."""
    overrides: dict[str, Any] = {}
    for section_name, section in data.items():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            toml_key = f"{section_name}.{key}"
            field_name = _TOML_MAP.get(toml_key)
            if field_name is not None:
                overrides[field_name] = value
            else:
                _logger.debug("itsme config: unknown TOML key %r, ignoring", toml_key)
    return overrides


# ---------------------------------------------------------------- loader


def load_config(
    *,
    config_path: Path | None = None,
    skip_file: bool = False,
    skip_env: bool = False,
) -> Config:
    """Build an immutable :class:`Config` by merging sources.

    Resolution order (highest priority first):

    1. Environment variables
    2. Config file (``~/.itsme/config.toml``)
    3. Built-in defaults

    Args:
        config_path: Override the config file path (default:
            ``~/.itsme/config.toml``).
        skip_file: Don't read the config file (for tests).
        skip_env: Don't read env vars (for tests).

    Returns:
        Frozen :class:`Config` snapshot.
    """
    path = config_path or DEFAULT_CONFIG_PATH

    # Start with defaults (from the dataclass)
    merged: dict[str, Any] = {}

    # Layer 1: TOML file (lowest priority override)
    if not skip_file:
        file_overrides = _read_toml(path)
        merged.update(file_overrides)

    # Layer 2: Environment variables (highest priority override)
    if not skip_env:
        env_overrides = _read_env()
        merged.update(env_overrides)

    # Record which file was used
    if not skip_file and path.is_file():
        merged["_config_file"] = str(path)

    return Config(**merged)
