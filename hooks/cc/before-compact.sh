#!/usr/bin/env bash
# itsme PreCompact hook — snapshots transcript before CC compacts.
set -u
python -m itsme.hooks before-compact
