#!/usr/bin/env bash
# itsme SessionEnd hook — snapshots transcript tail into events ring.
# Thin shim; logic lives in itsme.hooks.lifecycle.
set -u
python3 -m itsme.hooks before-exit
