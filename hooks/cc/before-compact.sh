#!/usr/bin/env bash
# itsme PreCompact hook — snapshots transcript before CC compacts.
# See before-exit.sh for the `uv run` rationale + always-exit-0 contract.
set -u

if ! command -v uv >/dev/null 2>&1; then
    echo "itsme hook: 'uv' not found on PATH; skipping capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
    exit 0
fi

if ! uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m itsme.hooks before-compact; then
    echo "itsme hook: bootstrap failed; continuing without capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
fi
exit 0
