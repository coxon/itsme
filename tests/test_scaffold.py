"""Scaffold smoke tests — confirm package wiring is sane.

Real behavior tests come with each implementation task.
"""

from __future__ import annotations

import itsme


def test_package_importable() -> None:
    assert itsme is not None


def test_version_string() -> None:
    assert isinstance(itsme.__version__, str)
    assert itsme.__version__.startswith("0.")


def test_subpackages_importable() -> None:
    """All scaffolded subpackages must import without error."""
    import itsme.core  # noqa: F401
    import itsme.core.adapters  # noqa: F401
    import itsme.core.aleph  # noqa: F401
    import itsme.core.events  # noqa: F401
    import itsme.core.workers  # noqa: F401
    import itsme.mcp  # noqa: F401
    import itsme.mcp.tools  # noqa: F401
