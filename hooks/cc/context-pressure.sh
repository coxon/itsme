#!/usr/bin/env bash
# itsme context-pressure hook — proactive salvage when ctx fills.
# Wired to both UserPromptSubmit and PostToolUse in hooks.json;
# same command, debounce state ensures we don't over-capture.
set -u
python -m itsme.hooks context-pressure
