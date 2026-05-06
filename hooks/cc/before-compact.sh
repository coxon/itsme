#!/usr/bin/env bash
# itsme PreCompact hook — snapshots transcript before CC compacts.
# See before-exit.sh for the `uv run` rationale.
set -u
exec uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m itsme.hooks before-compact
