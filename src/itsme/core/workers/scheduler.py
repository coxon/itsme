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
        # Captured if ``_run`` blows up before signaling ``_started``,
        # so ``start()`` can surface a helpful message instead of
        # leaving an unhandled-thread-exception warning.
        self._startup_error: BaseException | None = None

    def add_worker(self, fn: WorkerFn) -> None:
        """Register a worker callable. Must be added before :meth:`start`."""
        if self._thread is not None:
            raise RuntimeError("cannot add workers after start()")
        self._workers.append(fn)

    def start(self) -> None:
        """Spawn the background thread and run all workers concurrently.

        Raises:
            RuntimeError: The background thread failed to signal
                ``_started`` within the boot timeout — usually means a
                worker raised synchronously inside ``_run`` before the
                loop could come up.
        """
        if self._thread is not None:
            raise RuntimeError("scheduler already started")

        self._thread = threading.Thread(target=self._run, name="itsme-scheduler", daemon=True)
        self._thread.start()
        # Wait until the loop is running so the caller can submit calls.
        # Don't trust a False return + dead thread — that means _run()
        # blew up before set(); raising here beats handing the caller a
        # silently broken scheduler.
        if not self._started.wait(timeout=5):
            alive = self._thread.is_alive()
            cause = self._startup_error
            msg = (
                f"scheduler failed to start within 5s "
                f"(thread alive={alive}); check worker setup"
            )
            if cause is not None:
                raise RuntimeError(msg) from cause
            raise RuntimeError(msg)

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel all workers and join the thread.

        Idempotent: calling stop on a never-started or already-stopped
        scheduler is a no-op.

        Raises:
            TimeoutError: ``timeout`` elapsed but the thread is still
                alive. We deliberately do **not** mark the scheduler
                stopped in that case so a follow-up ``stop()`` can
                retry instead of silently masking a leaked thread.
        """
        if self._thread is None or self._stopped.is_set():
            return
        loop = self._loop
        if loop is not None:
            for task in self._tasks:
                loop.call_soon_threadsafe(task.cancel)
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise TimeoutError(
                f"scheduler thread did not exit within {timeout}s; not marking stopped"
            )
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
        except BaseException as exc:  # noqa: BLE001
            # Capture so ``start()`` can chain it; if we let the thread
            # die with an uncaught exception pytest surfaces a noisy
            # PytestUnhandledThreadExceptionWarning and operators see a
            # bare traceback in the log without context.
            if not self._started.is_set():
                self._startup_error = exc
        finally:
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            # Drain cancellations so the loop closes cleanly.
            with contextlib.suppress(RuntimeError):
                loop.run_until_complete(asyncio.gather(*self._tasks, return_exceptions=True))
            loop.close()
