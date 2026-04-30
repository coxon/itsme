"""wing/room slug helpers (ARCHITECTURE §7.1, ROADMAP T1.14).

itsme isolates its writes inside MemPalace by **always** prefixing
namespaces:

    wing_<project-slug>      e.g. ``wing_itsme``, ``wing_my-app``
    room_<topic-slug>        e.g. ``room_decisions``, ``room_general``

This way a single MemPalace instance can hold drawers for many projects
without itsme stepping on other consumers' wings, and ``status`` /
``ask`` can scope queries cleanly.
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WING_PREFIX = "wing_"
_ROOM_PREFIX = "room_"


def _slug(raw: str) -> str:
    """Lowercase, ASCII-only, dash-separated. Empty input is rejected."""
    cleaned = _SLUG_RE.sub("-", raw.lower()).strip("-")
    if not cleaned:
        raise ValueError(f"cannot produce a slug from {raw!r}")
    return cleaned


def wing(project: str) -> str:
    """Return the canonical ``wing_<slug>`` for *project*.

    Already-prefixed input (``wing_foo`` / ``WING_foo``) round-trips so
    callers can pass either a raw project name or a previously-formatted
    wing without double-prefixing.
    """
    if project.casefold().startswith(_WING_PREFIX):
        # Validate the suffix is itself a clean slug.
        return _WING_PREFIX + _slug(project[len(_WING_PREFIX) :])
    return _WING_PREFIX + _slug(project)


def room(topic: str) -> str:
    """Return the canonical ``room_<slug>`` for *topic*."""
    if topic.casefold().startswith(_ROOM_PREFIX):
        return _ROOM_PREFIX + _slug(topic[len(_ROOM_PREFIX) :])
    return _ROOM_PREFIX + _slug(topic)
