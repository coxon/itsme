#!/usr/bin/env bash
# itsme context-pressure hook — proactive salvage when ctx fills.
# Wired to both UserPromptSubmit and PostToolUse in hooks.json;
# same command, debounce state ensures we don't over-capture.
# See before-exit.sh for the `uv run` rationale + always-exit-0 contract.
set -u

plugin_root="${CLAUDE_PLUGIN_ROOT:-}"
if [[ -z "${plugin_root}" ]]; then
    echo "itsme hook: CLAUDE_PLUGIN_ROOT unset; skipping capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "itsme hook: 'uv' not found on PATH; skipping capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
    exit 0
fi

if ! uv run --project "${plugin_root}" python -m itsme.hooks context-pressure; then
    echo "itsme hook: bootstrap failed; continuing without capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
fi
exit 0
