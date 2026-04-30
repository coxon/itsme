"""Workers — 4 small brains (ARCHITECTURE §6).

router    raw -> route by kind
promoter  consolidation boundary -> Aleph wiki
curator   dedup / KG invalidate
reader    serve `ask`
"""

from itsme.core.workers.router import KIND_TO_ROOM, Router, RouterDecision
from itsme.core.workers.scheduler import WorkerScheduler

__all__ = [
    "KIND_TO_ROOM",
    "Router",
    "RouterDecision",
    "WorkerScheduler",
]
