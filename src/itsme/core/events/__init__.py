"""EventBus — single nervous system.

Six narrow event types live in :mod:`itsme.core.events.schema`
(ARCHITECTURE §5). Producers emit through :class:`EventBus`; consumers
poll via :meth:`EventBus.since` or :meth:`EventBus.tail`.
"""

from itsme.core.events.bus import EventBus
from itsme.core.events.schema import EventEnvelope, EventType

__all__ = ["EventBus", "EventEnvelope", "EventType"]
