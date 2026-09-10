import asyncio
import contextlib
import threading

import pytest

from kodi_mcp_server.orchestration_locking import (
    OrchestrationLockTimeout,
    acquire_global_workflow_lock,
)


def _hold_global(ready: threading.Event, release: threading.Event) -> None:
    with acquire_global_workflow_lock(timeout=1):
        ready.set()
        release.wait(5)


async def _heartbeat_while(coro) -> int:
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(heartbeat())
    try:
        with pytest.raises(OrchestrationLockTimeout):
            await coro
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return ticks


def test_repository_bootstrap_lock_wait_does_not_block_event_loop(monkeypatch):
    import kodi_mcp_server.repository_bootstrap as module

    ready = threading.Event()
    release = threading.Event()
    holder = threading.Thread(target=_hold_global, args=(ready, release))
    holder.start()
    assert ready.wait(2)
    monkeypatch.setenv("KODI_MCP_ORCHESTRATION_LOCK_TIMEOUT_SECONDS", "0.2")

    def blocking_build(**kwargs):
        with acquire_global_workflow_lock():
            raise AssertionError("must not acquire")

    monkeypatch.setattr(module, "build_repo_addon", blocking_build)
    async def exercise():
        return await _heartbeat_while(module.install_repository_bootstrap(object()))

    try:
        ticks = asyncio.run(exercise())
    finally:
        release.set()
        holder.join(2)

    assert ticks >= 5


def test_legacy_build_stage_lock_wait_does_not_block_event_loop(monkeypatch):
    import kodi_mcp_server.managed_addons as module

    ready = threading.Event()
    release = threading.Event()
    holder = threading.Thread(target=_hold_global, args=(ready, release))
    holder.start()
    assert ready.wait(2)
    monkeypatch.setenv("KODI_MCP_ORCHESTRATION_LOCK_TIMEOUT_SECONDS", "0.2")

    def blocking_build(**kwargs):
        with acquire_global_workflow_lock():
            raise AssertionError("must not acquire")

    monkeypatch.setattr(module, "build_dev_repo_zip", blocking_build)

    async def exercise():
        return await _heartbeat_while(module.build_and_stage_dev_repo_zip())

    try:
        ticks = asyncio.run(exercise())
    finally:
        release.set()
        holder.join(2)

    assert ticks >= 5
