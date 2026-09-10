import asyncio
import contextlib
import contextvars
import concurrent.futures
import multiprocessing
import os
from pathlib import Path

import pytest

from kodi_mcp_server.orchestration_locking import (
    LockOrderViolation,
    OrchestrationLockTimeout,
    acquire_global_workflow_lock,
    acquire_global_workflow_lock_async,
    acquire_target_mutation_lock,
    configured_lock_timeout_seconds,
)


def _hold_global(lock_root: str, ready, release) -> None:
    with acquire_global_workflow_lock(lock_root=Path(lock_root), timeout=2):
        ready.set()
        release.wait(5)


def _hold_target(lock_root: str, mutation_domain_id: str, ready, release) -> None:
    with acquire_target_mutation_lock(
        mutation_domain_id, lock_root=Path(lock_root), timeout=2
    ):
        ready.set()
        release.wait(5)


def _acquire_after_signal(lock_root: str, mutation_domain_id: str, ready, acquired) -> None:
    ready.wait(5)
    with acquire_target_mutation_lock(
        mutation_domain_id, lock_root=Path(lock_root), timeout=2
    ):
        acquired.set()


def _acquire_and_exit(lock_root: str, ready) -> None:
    with acquire_global_workflow_lock(lock_root=Path(lock_root), timeout=2):
        ready.set()
        os._exit(0)


def _context():
    return multiprocessing.get_context("spawn")


def test_global_lock_is_cross_process_timeout_is_stable_and_work_never_starts(tmp_path):
    ctx = _context()
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_global, args=(str(tmp_path), ready, release))
    holder.start()
    assert ready.wait(5)

    protected_work_started = False
    with pytest.raises(OrchestrationLockTimeout) as exc_info:
        with acquire_global_workflow_lock(lock_root=tmp_path, timeout=0.1):
            protected_work_started = True

    assert protected_work_started is False
    assert exc_info.value.error_code == "ORCHESTRATION_LOCK_TIMEOUT"
    assert exc_info.value.to_error() == {
        "error_code": "ORCHESTRATION_LOCK_TIMEOUT",
        "lock_scope": "global_workflow",
        "message": "timed out waiting for global workflow lock",
    }

    release.set()
    holder.join(5)
    assert holder.exitcode == 0
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        pass


def test_abandoned_direct_async_enter_is_released_when_owner_task_finishes(tmp_path):
    import gc
    import kodi_mcp_server.orchestration_locking as module

    contexts = []

    async def exercise():
        async def abandon():
            context = acquire_global_workflow_lock_async(
                lock_root=tmp_path, timeout=1
            )
            await context.__aenter__()
            contexts.append(context)
            # Deliberately omit __aexit__.

        owner = asyncio.create_task(abandon())
        await owner
        del owner
        gc.collect()
        await asyncio.sleep(0)

        async def subsequent():
            async with acquire_global_workflow_lock_async(
                lock_root=tmp_path, timeout=0.5
            ):
                return True

        assert await asyncio.create_task(subsequent()) is True

    asyncio.run(exercise())
    assert contexts[0]._fd is None
    assert module._HELD_LOCKS == {}


def test_lock_releases_on_process_exit_and_persistent_file_is_not_stale_ownership(tmp_path):
    ctx = _context()
    ready = ctx.Event()
    holder = ctx.Process(target=_acquire_and_exit, args=(str(tmp_path), ready))
    holder.start()
    assert ready.wait(5)
    holder.join(5)
    assert holder.exitcode == 0

    lock_file = tmp_path / "global-workflow.lock"
    assert lock_file.is_file()
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        assert lock_file.is_file()
    assert lock_file.is_file()


def test_aliases_sharing_mutation_domain_serialize(tmp_path):
    ctx = _context()
    ready = ctx.Event()
    release = ctx.Event()
    acquired = ctx.Event()
    holder = ctx.Process(
        target=_hold_target,
        args=(str(tmp_path), "living-room", ready, release),
    )
    waiter = ctx.Process(
        target=_acquire_after_signal,
        args=(str(tmp_path), "living-room", ready, acquired),
    )
    holder.start()
    waiter.start()
    assert ready.wait(5)
    assert acquired.wait(0.2) is False
    release.set()
    assert acquired.wait(5)
    holder.join(5)
    waiter.join(5)
    assert holder.exitcode == waiter.exitcode == 0


def test_different_mutation_domains_can_proceed_concurrently(tmp_path):
    ctx = _context()
    ready = ctx.Event()
    release = ctx.Event()
    acquired = ctx.Event()
    holder = ctx.Process(
        target=_hold_target,
        args=(str(tmp_path), "living-room", ready, release),
    )
    other = ctx.Process(
        target=_acquire_after_signal,
        args=(str(tmp_path), "bedroom", ready, acquired),
    )
    holder.start()
    other.start()
    assert ready.wait(5)
    assert acquired.wait(5)
    release.set()
    holder.join(5)
    other.join(5)
    assert holder.exitcode == other.exitcode == 0


def test_lock_order_forbids_server_lock_while_target_lock_is_held(tmp_path):
    with acquire_target_mutation_lock("living-room", lock_root=tmp_path, timeout=1):
        with pytest.raises(LockOrderViolation, match="global.*target"):
            with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
                pass


def test_lock_order_requires_server_lock_release_before_target_lock(tmp_path):
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        with pytest.raises(LockOrderViolation, match="target.*global"):
            with acquire_target_mutation_lock(
                "living-room", lock_root=tmp_path, timeout=1
            ):
                pass


def test_target_lock_filename_never_contains_raw_domain(tmp_path):
    with acquire_target_mutation_lock("living-room", lock_root=tmp_path, timeout=1):
        files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("target-")
    assert "living-room" not in files[0].name


def test_async_lock_wait_does_not_block_event_loop(tmp_path):
    ctx = _context()
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_global, args=(str(tmp_path), ready, release))
    holder.start()
    assert ready.wait(5)

    async def exercise():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            with pytest.raises(OrchestrationLockTimeout):
                async with acquire_global_workflow_lock_async(
                    lock_root=tmp_path, timeout=0.15
                ):
                    pass
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return ticks

    loop = asyncio.new_event_loop()
    try:
        ticks = loop.run_until_complete(exercise())
    finally:
        loop.close()
        release.set()
        holder.join(5)

    assert holder.exitcode == 0
    assert ticks >= 5


def test_async_wait_cancellation_never_retains_lock(tmp_path):
    ctx = _context()
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_global, args=(str(tmp_path), ready, release))
    holder.start()
    assert ready.wait(5)

    async def exercise():
        entered = asyncio.Event()
        lock_context = acquire_global_workflow_lock_async(
            lock_root=tmp_path, timeout=2
        )

        async def waiter():
            entered.set()
            async with lock_context:
                raise AssertionError("cancelled waiter acquired the lock")

        task = asyncio.create_task(waiter())
        await entered.wait()
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert lock_context._fd is None

    asyncio.run(exercise())
    release.set()
    holder.join(5)
    assert holder.exitcode == 0
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        pass


def test_same_task_nested_async_lock_is_reentrant_and_depth_balances(tmp_path):
    async def exercise():
        async with acquire_global_workflow_lock_async(lock_root=tmp_path, timeout=1):
            async with acquire_global_workflow_lock_async(lock_root=tmp_path, timeout=1):
                pass
        async with acquire_global_workflow_lock_async(lock_root=tmp_path, timeout=1):
            pass

    asyncio.run(exercise())


def test_same_sync_thread_nested_lock_is_reentrant_and_depth_balances(tmp_path):
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
            pass
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        pass


def test_lock_timeout_default_is_central_configurable_and_bounded(monkeypatch):
    monkeypatch.delenv("KODI_MCP_ORCHESTRATION_LOCK_TIMEOUT_SECONDS", raising=False)
    assert configured_lock_timeout_seconds() == 60.0
    monkeypatch.setenv("KODI_MCP_ORCHESTRATION_LOCK_TIMEOUT_SECONDS", "90")
    assert configured_lock_timeout_seconds() == 90.0
    monkeypatch.setenv("KODI_MCP_ORCHESTRATION_LOCK_TIMEOUT_SECONDS", "9999")
    with pytest.raises(ValueError, match="0 to 3600"):
        configured_lock_timeout_seconds()


def test_child_task_cannot_inherit_parent_reentrancy(tmp_path):
    async def exercise():
        async with acquire_global_workflow_lock_async(lock_root=tmp_path, timeout=1):
            async def child():
                with pytest.raises(OrchestrationLockTimeout):
                    async with acquire_global_workflow_lock_async(
                        lock_root=tmp_path, timeout=0.1
                    ):
                        pass

            await asyncio.create_task(child())
        async with acquire_global_workflow_lock_async(lock_root=tmp_path, timeout=1):
            pass

    asyncio.run(exercise())


def test_copied_context_in_another_thread_is_not_reentrant(tmp_path):
    copied = None
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        copied = contextvars.copy_context()

        def contender():
            with pytest.raises(OrchestrationLockTimeout):
                with acquire_global_workflow_lock(lock_root=tmp_path, timeout=0.1):
                    pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copied.run, contender).result(timeout=2)

    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        pass


def test_cancellation_immediately_after_successful_try_releases_lock(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.orchestration_locking as module

    original = module._try_lock
    contexts = []

    async def holder():
        task = asyncio.current_task()
        lock_context = acquire_global_workflow_lock_async(
            lock_root=tmp_path, timeout=1
        )
        contexts.append(lock_context)

        def acquire_then_cancel(fd):
            acquired = original(fd)
            if acquired:
                task.cancel()
            return acquired

        monkeypatch.setattr(module, "_try_lock", acquire_then_cancel)
        async with lock_context:
            await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(holder())
    assert contexts[0]._fd is None
    monkeypatch.setattr(module, "_try_lock", original)
    with acquire_global_workflow_lock(lock_root=tmp_path, timeout=1):
        pass
