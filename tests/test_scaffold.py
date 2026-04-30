"""Scaffold smoke tests — confirm package wiring is sane.

Real behavior tests come with each implementation task.
"""

from __future__ import annotations

import itsme


def test_package_importable() -> None:
    """Top-level `itsme` package must import without error."""
    assert itsme is not None


def test_version_string() -> None:
    """`__version__` must be a string in the 0.x family (pre-1.0)."""
    assert isinstance(itsme.__version__, str)
    assert itsme.__version__.startswith("0.")


def test_subpackages_importable() -> None:
    """All scaffolded subpackages must import without error."""
    import itsme.core  # noqa: F401
    import itsme.core.adapters  # noqa: F401
    import itsme.core.adapters.mempalace  # noqa: F401
    import itsme.core.adapters.naming  # noqa: F401
    import itsme.core.aleph  # noqa: F401
    import itsme.core.api  # noqa: F401
    import itsme.core.events  # noqa: F401
    import itsme.core.events.bus  # noqa: F401
    import itsme.core.events.ringbuf  # noqa: F401
    import itsme.core.events.schema  # noqa: F401
    import itsme.core.llm  # noqa: F401
    import itsme.core.workers  # noqa: F401
    import itsme.core.workers.router  # noqa: F401
    import itsme.core.workers.scheduler  # noqa: F401
    import itsme.mcp  # noqa: F401
    import itsme.mcp.tools  # noqa: F401
    import itsme.mcp.tools.ask  # noqa: F401
    import itsme.mcp.tools.remember  # noqa: F401
    import itsme.mcp.tools.status  # noqa: F401


def test_mcp_server_entrypoint_importable() -> None:
    """`python -m itsme.mcp.server` path must be reachable.

    Guards the plugin.json launch path: if packaging or namespace
    breaks, we catch it here before runtime.
    """
    import itsme.mcp.server

    assert callable(itsme.mcp.server.main)
    assert callable(itsme.mcp.server.build_server)
