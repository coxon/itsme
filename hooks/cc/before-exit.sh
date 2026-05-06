#!/usr/bin/env bash
# itsme SessionEnd hook — snapshots transcript tail into events ring.
# Thin shim; logic lives in itsme.hooks.lifecycle.
#
# Uses `uv run --project ${CLAUDE_PLUGIN_ROOT}` so the plugin's deps
# resolve from its own pyproject.toml regardless of how / where CC's
# host Python is installed. First fire after install pays a one-time
# `uv sync` cost (~5-10s) — covered by the 15s timeout in hooks.json.
# Subsequent fires reuse the cached venv (~50-100ms overhead).
set -u
exec uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m itsme.hooks before-exit
