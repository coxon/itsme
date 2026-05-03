"""Real-binary smoke for StdioMemPalaceAdapter.

Skipped unless ``python3 -m mempalace.mcp_server`` can actually spawn.
Runs one write + one search against an isolated palace directory so
the dev's main palace is untouched.

Run manually::

    uv run pytest tests/smoke/test_mempalace_stdio_roundtrip.py -v

Or set ``ITSME_SMOKE_MEMPALACE=1`` in CI on hosts that have MemPalace
installed (no such host exists today — T1.13.5 lands the adapter with
the fake-server unit tests only; this file future-proofs the real wire
when someone adds MemPalace to the CI runner image).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Soft-import: module lives in the source tree regardless of install.
from itsme.core.adapters.mempalace_stdio import StdioMemPalaceAdapter


def _mempalace_importable() -> bool:
    """Best-effort check: can ``python3 -m mempalace.mcp_server --help`` boot?"""
    python = shutil.which("python3") or sys.executable
    try:
        proc = subprocess.run(
            [python, "-c", "import mempalace.mcp_server"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


_HAS_MEMPALACE = _mempalace_importable()


@pytest.mark.skipif(
    not _HAS_MEMPALACE,
    reason="MemPalace not installed on this host — unit tests cover the adapter",
)
def test_real_mempalace_write_search_roundtrip(tmp_path: Path) -> None:
    """Spawn the real MemPalace binary and round-trip one drawer."""
    env = dict(os.environ)
    # Isolate: write into tmp dir, not the user's real palace.
    env["MEMPALACE_PALACE_PATH"] = str(tmp_path / "palace")

    a = StdioMemPalaceAdapter(env=env, handshake_timeout_s=15.0, call_timeout_s=20.0)
    try:
        res = a.write(
            content="itsme smoke: stdio adapter real-binary roundtrip",
            wing="itsme-smoke",
            room="roundtrip",
        )
        assert res.drawer_id

        hits = a.search("itsme smoke roundtrip", wing="itsme-smoke")
        # ChromaDB may or may not score the exact phrase highest; we just
        # need the drawer to come back at all.
        assert hits, "search returned nothing — drawer didn't persist"
        assert any("itsme smoke" in h.content for h in hits)
    finally:
        a.close()
