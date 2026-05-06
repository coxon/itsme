#!/usr/bin/env bash
# itsme SessionEnd hook — snapshots transcript tail into events ring.
# Thin shim; logic lives in itsme.hooks.lifecycle.
#
# Uses `uv run --project ${CLAUDE_PLUGIN_ROOT}` so the plugin's deps
# resolve from its own pyproject.toml regardless of how / where CC's
# host Python is installed. First fire after install pays a one-time
# `uv sync` cost (~5-10s) — covered by the 15s timeout in hooks.json.
# Subsequent fires reuse the cached venv (~50-100ms overhead).
#
# Contract (per docs/INSTALL.md "Hook contract"): the shim ALWAYS
# exits 0 and emits a `{"continue": true, "suppressOutput": true}`
# envelope on stdout. Bash- or uv-layer failures get logged to
# stderr but never bubble out as a red error in the CC UI — passive
# capture must not block the IDE. The Python module itself already
# traps internal errors and emits the envelope; this guard only
# covers the case where the Python module never gets to run (uv not
# installed, sync failure, etc.).
set -u

if ! command -v uv >/dev/null 2>&1; then
    echo "itsme hook: 'uv' not found on PATH; skipping capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
    exit 0
fi

if ! uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m itsme.hooks before-exit; then
    echo "itsme hook: bootstrap failed; continuing without capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
fi
exit 0
