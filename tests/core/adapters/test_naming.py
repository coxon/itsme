"""wing/room slug invariants (ROADMAP T1.14)."""

from __future__ import annotations

import pytest

from itsme.core.adapters.naming import room, wing


def test_wing_slugifies_simple_name() -> None:
    """ASCII project name → ``wing_<lower-dashed>``."""
    assert wing("MyApp") == "wing_myapp"
    assert wing("Hello World") == "wing_hello-world"


def test_wing_collapses_punctuation() -> None:
    """Adjacent non-alnum chars collapse to a single dash."""
    assert wing("foo / bar.baz") == "wing_foo-bar-baz"


def test_wing_strips_edge_dashes() -> None:
    """Leading/trailing punctuation does not produce empty fragments."""
    assert wing("__cool__") == "wing_cool"


def test_wing_idempotent_on_already_prefixed() -> None:
    """``wing_foo`` round-trips unchanged."""
    assert wing("wing_foo") == "wing_foo"
    assert wing("wing_FOO") == "wing_foo"


def test_wing_rejects_empty_after_slug() -> None:
    """A name that slugifies to empty must error, not produce ``wing_``."""
    with pytest.raises(ValueError, match="slug"):
        wing("!!!")


def test_room_basic() -> None:
    """``room`` mirrors ``wing`` semantics."""
    assert room("decisions") == "room_decisions"
    assert room("My Topic") == "room_my-topic"
    assert room("room_general") == "room_general"


def test_wing_and_room_use_distinct_prefixes() -> None:
    """Naming collision guard — wing/room namespaces must not overlap."""
    assert wing("x").startswith("wing_")
    assert room("x").startswith("room_")
    assert wing("x") != room("x")
