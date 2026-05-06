#!/usr/bin/env bash
# itsme context-pressure hook — proactive salvage when ctx fills.
# Wired to both UserPromptSubmit and PostToolUse in hooks.json;
# same command, debounce state ensures we don't over-capture.
# See before-exit.sh for the `uv run` rationale.
set -u
exec uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m itsme.hooks context-pressure
