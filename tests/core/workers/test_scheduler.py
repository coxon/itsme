"""WorkerScheduler — threaded asyncio loop for background workers (T1.16)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from itsme.core.workers.scheduler import WorkerScheduler


def test_start_then_stop_runs_worker_once() -> None:
    """Worker fn is awaited once when the scheduler starts."""
    ran = threading.Event()

    async def worker() -> None:
        ran.set()
        # keep the loop alive long enough for stop() to find tasks
        await asyncio.sleep(60)

    sched = WorkerScheduler()
    sched.add_worker(worker)
    sched.start()
    try:
        assert ran.wait(timeout=2), "worker did not start"
    finally:
        sched.stop()


def test_stop_is_idempotent() -> None:
    """Calling stop twice (or before start) is safe."""
    sched = WorkerScheduler()
    # never started
    sched.stop()

    async def worker() -> None:
        await asyncio.sleep(60)

    sched.add_worker(worker)
    sched.start()
    sched.stop()
    sched.stop()  # second call is a no-op


def test_add_worker_after_start_raises() -> None:
    """Adding workers after start is a programming error."""

    async def worker() -> None:
        await asyncio.sleep(60)

    sched = WorkerScheduler()
    sched.add_worker(worker)
    sched.start()
    try:
        with pytest.raises(RuntimeError, match="after start"):
            sched.add_worker(worker)
    finally:
        sched.stop()


def test_double_start_raises() -> None:
    """Re-starting an already-running scheduler is a programming error."""

    async def worker() -> None:
        await asyncio.sleep(60)

    sched = WorkerScheduler()
    sched.add_worker(worker)
    sched.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            sched.start()
    finally:
        sched.stop()


def test_multiple_workers_run_concurrently() -> None:
    """All registered workers run on the same loop."""
    counter = {"a": 0, "b": 0}
    started = threading.Event()
    started_b = threading.Event()

    async def w_a() -> None:
        counter["a"] += 1
        started.set()
        await asyncio.sleep(60)

    async def w_b() -> None:
        counter["b"] += 1
        started_b.set()
        await asyncio.sleep(60)

    sched = WorkerScheduler()
    sched.add_worker(w_a)
    sched.add_worker(w_b)
    sched.start()
    try:
        assert started.wait(timeout=2)
        assert started_b.wait(timeout=2)
        assert counter == {"a": 1, "b": 1}
    finally:
        sched.stop()


def test_stop_cancels_running_tasks() -> None:
    """Long-running workers are cancelled on stop."""
    cancelled = threading.Event()

    async def worker() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    sched = WorkerScheduler()
    sched.add_worker(worker)
    sched.start()
    time.sleep(0.05)
    sched.stop(timeout=2)
    assert cancelled.wait(timeout=2)


def test_start_raises_runtime_error_when_worker_setup_fails() -> None:
    """start() must NOT silently succeed when ``_run`` dies pre-``_started.set``.

    Regression for CodeRabbit PR#6 r2 finding — a worker that returns
    something asyncio refuses (here: ``None`` from a non-async lambda)
    blows up inside ``loop.create_task(fn())`` before the started
    event is set. Earlier code ignored ``_started.wait()`` return
    value and handed the caller a dead scheduler.
    """
    sched = WorkerScheduler()
    # Not a coroutine factory; create_task(None) raises inside _run.
    sched.add_worker(lambda: None)  # type: ignore[arg-type,return-value]

    with pytest.raises(RuntimeError, match="failed to start") as exc_info:
        sched.start()
    # The original TypeError from create_task is chained as ``__cause__``
    # so operators can diagnose without digging through thread logs.
    assert isinstance(exc_info.value.__cause__, TypeError)
