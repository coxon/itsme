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


def _detect_mempalace_python() -> str | None:
    """Return the interpreter that can ``import mempalace.mcp_server``.

    We probe with the same interpreter we'll later spawn the server
    with, otherwise a host with two pythons (system ``python3`` without
    MemPalace + a venv ``python`` with it, or vice-versa) would pass
    the probe and then fail to boot. Prefers ``which python3`` →
    falls back to ``sys.executable``. Returns ``None`` if neither
    interpreter can import MemPalace.
    """
    candidates: list[str] = []
    p3 = shutil.which("python3")
    if p3:
        candidates.append(p3)
    if sys.executable and sys.executable not in candidates:
        candidates.append(sys.executable)

    for python in candidates:
        try:
            proc = subprocess.run(
                [python, "-c", "import mempalace.mcp_server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode == 0:
            return python
    return None


_MEMPALACE_PYTHON = _detect_mempalace_python()


@pytest.mark.skipif(
    _MEMPALACE_PYTHON is None,
    reason="MemPalace not installed on this host — unit tests cover the adapter",
)
def test_real_mempalace_write_search_roundtrip(tmp_path: Path) -> None:
    """Spawn the real MemPalace binary and round-trip one drawer."""
    env = dict(os.environ)
    # Isolate: write into tmp dir, not the user's real palace.
    env["MEMPALACE_PALACE_PATH"] = str(tmp_path / "palace")

    # _MEMPALACE_PYTHON is None-guarded by skipif; assert for type narrowing.
    assert _MEMPALACE_PYTHON is not None
    a = StdioMemPalaceAdapter(
        command=(_MEMPALACE_PYTHON, "-m", "mempalace.mcp_server"),
        env=env,
        handshake_timeout_s=15.0,
        call_timeout_s=20.0,
    )
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
