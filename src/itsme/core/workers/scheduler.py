"""Process-internal asyncio scheduler — T1.16.

v0.0.1 minimum: a small wrapper that owns the asyncio loop and
launches one task per worker. The MCP server stays synchronous (FastMCP
runs its own asyncio loop under the hood); this scheduler runs in a
**separate** thread so MCP request handling and background routing
don't interfere.

Why not just rely on FastMCP's loop?
--------------------------------------

FastMCP's stdio runtime owns its event loop and we don't want to
schedule arbitrary tasks on it (mixing "respond to tool call" and
"poll the events ring" on the same loop creates head-of-line
blocking). One thread per concern is simpler and matches v0.0.1's
"first cut" goal.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Coroutine
from typing import Any

#: A worker is a no-arg async function the scheduler will await.
WorkerFn = Callable[[], Coroutine[Any, Any, Any]]


class WorkerScheduler:
    """Owns a background thread + asyncio loop hosting workers.

    Usage::

        sched = WorkerScheduler()
        sched.add_worker(router.consume_loop)
        sched.start()
        ...
        sched.stop()
    """

    def __init__(self) -> None:
        self._workers: list[WorkerFn] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._started = threading.Event()
        self._stopped = threading.Event()

    def add_worker(self, fn: WorkerFn) -> None:
        """Register a worker callable. Must be added before :meth:`start`."""
        if self._thread is not None:
            raise RuntimeError("cannot add workers after start()")
        self._workers.append(fn)

    def start(self) -> None:
        """Spawn the background thread and run all workers concurrently."""
        if self._thread is not None:
            raise RuntimeError("scheduler already started")

        self._thread = threading.Thread(target=self._run, name="itsme-scheduler", daemon=True)
        self._thread.start()
        # Wait until the loop is running so the caller can submit calls.
        self._started.wait(timeout=5)

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel all workers and join the thread.

        Idempotent: calling stop on a never-started or already-stopped
        scheduler is a no-op.
        """
        if self._thread is None or self._stopped.is_set():
            return
        loop = self._loop
        if loop is not None:
            for task in self._tasks:
                loop.call_soon_threadsafe(task.cancel)
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=timeout)
        self._stopped.set()

    # ----------------------------------------------------------- internals
    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            for fn in self._workers:
                self._tasks.append(loop.create_task(fn()))
            self._started.set()
            loop.run_forever()
        finally:
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            # Drain cancellations so the loop closes cleanly.
            with contextlib.suppress(RuntimeError):
                loop.run_until_complete(asyncio.gather(*self._tasks, return_exceptions=True))
            loop.close()
