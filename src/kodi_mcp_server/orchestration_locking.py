"""Cross-process lock primitives for mutation-safe orchestration.

Lock files are persistent coordination names, not ownership records. Ownership is
held solely by the kernel lock on an open descriptor, so process exit releases a
lock without deleting its file.

Canonical ordering for future mutating orchestration is:

1. optional managed-addon lock;
2. global server workflow lock;
3. freeze server-side inputs and artifact identity;
4. release server/addon locks;
5. per-target mutation lock;
6. target mutation and readback.

Reentrancy is limited to the same asyncio task (when present), OS thread, and lock
key. Context copied into a child task or another thread cannot bypass ``flock``.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import math
import os
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import Literal

LockScope = Literal["global_workflow", "target_mutation"]
Owner = tuple[asyncio.Task | None, int]
_HELD_LOCKS: dict[tuple[str, Owner], tuple[LockScope, int]] = {}
_HELD_LOCKS_GUARD = threading.RLock()
_POLL_SECONDS = 0.025
_MAX_TIMEOUT_SECONDS = 3600.0
_DEFAULT_TIMEOUT_SECONDS = 60.0
_TIMEOUT_ENV = "KODI_MCP_ORCHESTRATION_LOCK_TIMEOUT_SECONDS"
LOCK_TIMEOUT_HTTP_STATUS = 423


class LockOrderViolation(RuntimeError):
    """Raised before acquisition when canonical lock ordering would be violated."""


class OrchestrationLockTimeout(TimeoutError):
    """Bounded, safe lock acquisition failure."""

    error_code = "ORCHESTRATION_LOCK_TIMEOUT"

    def __init__(self, scope: LockScope):
        self.lock_scope = scope
        label = "global workflow" if scope == "global_workflow" else "target mutation"
        super().__init__(f"timed out waiting for {label} lock")

    def to_error(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "lock_scope": self.lock_scope,
            "message": str(self),
        }


def lock_timeout_http_payload(
    exc: OrchestrationLockTimeout, *, request_id: str | None = None
) -> dict[str, object]:
    """Return the shared sanitized legacy-HTTP resource-busy envelope."""

    return {
        "request_id": request_id,
        "result": None,
        "error": str(exc),
        "error_type": "resource_busy",
        "error_code": exc.error_code,
        "latency_ms": None,
    }


def _validated_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("lock timeout must be a number")
    value = float(timeout)
    if not math.isfinite(value) or not 0 <= value <= _MAX_TIMEOUT_SECONDS:
        raise ValueError("lock timeout must be between 0 and 3600 seconds")
    return value


def configured_lock_timeout_seconds() -> float:
    """Return the bounded configured wait, defaulting to sixty seconds."""

    raw = os.getenv(_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        return _validated_timeout(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{_TIMEOUT_ENV} must be a number from 0 to 3600") from exc


def get_orchestration_lock_root() -> Path:
    """Return the one canonical application lock root."""

    from kodi_mcp_server.paths import PROJECT_ROOT

    return PROJECT_ROOT / "project" / "orchestration-locks"


def _lock_path(scope: LockScope, lock_root: Path, mutation_domain_id: str | None) -> Path:
    root = Path(lock_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if scope == "global_workflow":
        return root / "global-workflow.lock"
    if not isinstance(mutation_domain_id, str) or not mutation_domain_id:
        raise ValueError("mutation_domain_id is required")
    digest = hashlib.sha256(mutation_domain_id.encode("utf-8")).hexdigest()
    return root / f"target-{digest}.lock"


def _owner_identity() -> Owner:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return task, threading.get_ident()


def _check_order(scope: LockScope, path: Path, owner: Owner) -> bool:
    path_key = str(path.resolve())
    with _HELD_LOCKS_GUARD:
        if (path_key, owner) in _HELD_LOCKS:
            return True
        scopes = {
            existing_scope
            for (_, existing_owner), (existing_scope, _) in _HELD_LOCKS.items()
            if existing_owner == owner
        }
    if scope == "global_workflow" and "target_mutation" in scopes:
        raise LockOrderViolation(
            "global workflow lock cannot be acquired while a target mutation lock is held"
        )
    if scope == "target_mutation" and "global_workflow" in scopes:
        raise LockOrderViolation(
            "target mutation lock requires the global workflow lock to be released first"
        )
    return False


def _record_acquisition(path: Path, scope: LockScope, owner: Owner) -> None:
    key = (str(path.resolve()), owner)
    with _HELD_LOCKS_GUARD:
        existing = _HELD_LOCKS.get(key)
        _HELD_LOCKS[key] = (scope, 1 if existing is None else existing[1] + 1)


def _release_acquisition(path: Path, owner: Owner) -> None:
    key = (str(path.resolve()), owner)
    with _HELD_LOCKS_GUARD:
        scope, depth = _HELD_LOCKS[key]
        if depth == 1:
            del _HELD_LOCKS[key]
        else:
            _HELD_LOCKS[key] = (scope, depth - 1)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _release_fd(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class _FileLockContext:
    def __init__(
        self,
        *,
        scope: LockScope,
        timeout: float | None,
        lock_root: Path,
        mutation_domain_id: str | None = None,
    ) -> None:
        self.scope: LockScope = scope
        self.timeout = configured_lock_timeout_seconds() if timeout is None else _validated_timeout(timeout)
        self.path = _lock_path(scope, lock_root, mutation_domain_id)
        self._fd: int | None = None
        self._recorded = False
        self._owner: Owner | None = None
        self._reentrant = False
        self._task_done_callback = None

    def _arm_abandon_cleanup(self, owner: Owner) -> None:
        task = owner[0]
        if task is not None:
            self._task_done_callback = self._owner_task_done
            task.add_done_callback(self._task_done_callback)

    def _owner_task_done(self, task: asyncio.Task) -> None:
        self._task_done_callback = None
        if self._recorded:
            self._force_release()

    def _force_release(self) -> None:
        if self._recorded and self._owner is not None:
            _release_acquisition(self.path, self._owner)
            self._recorded = False
        if self._fd is not None:
            fd, self._fd = self._fd, None
            _release_fd(fd)
        self._owner = None

    def __enter__(self) -> "_FileLockContext":
        owner = _owner_identity()
        self._owner = owner
        self._reentrant = _check_order(self.scope, self.path, owner)
        if self._reentrant:
            _record_acquisition(self.path, self.scope, owner)
            self._recorded = True
            return self

        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise OrchestrationLockTimeout(self.scope)
                time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            self._fd = fd
            fd = -1
            _record_acquisition(self.path, self.scope, owner)
            self._recorded = True
            return self
        except BaseException:
            if fd >= 0:
                os.close(fd)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        owner = self._owner
        if (
            owner is not None
            and owner[0] is not None
            and self._task_done_callback is not None
        ):
            owner[0].remove_done_callback(self._task_done_callback)
            self._task_done_callback = None
        self._force_release()


class _AsyncFileLockContext(_FileLockContext):
    async def __aenter__(self) -> "_AsyncFileLockContext":
        owner = _owner_identity()
        self._owner = owner
        self._reentrant = _check_order(self.scope, self.path, owner)
        if self._reentrant:
            _record_acquisition(self.path, self.scope, owner)
            self._recorded = True
            self._arm_abandon_cleanup(owner)
            return self

        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise OrchestrationLockTimeout(self.scope)
                await asyncio.sleep(
                    min(_POLL_SECONDS, max(0.0, deadline - time.monotonic()))
                )
            # No await occurs between successful nonblocking flock and explicit
            # ownership transfer, so cancellation cannot strand an acquired fd.
            self._fd = fd
            fd = -1
            _record_acquisition(self.path, self.scope, owner)
            self._recorded = True
            self._arm_abandon_cleanup(owner)
            return self
        except BaseException:
            if fd >= 0:
                os.close(fd)
            if self._fd is not None:
                acquired, self._fd = self._fd, None
                _release_fd(acquired)
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, traceback)


def acquire_global_workflow_lock(
    *, timeout: float | None = None, lock_root: Path | None = None
) -> _FileLockContext:
    return _FileLockContext(
        scope="global_workflow",
        timeout=timeout,
        lock_root=lock_root if lock_root is not None else get_orchestration_lock_root(),
    )


def acquire_target_mutation_lock(
    mutation_domain_id: str,
    *,
    timeout: float | None = None,
    lock_root: Path | None = None,
) -> _FileLockContext:
    return _FileLockContext(
        scope="target_mutation",
        timeout=timeout,
        lock_root=lock_root if lock_root is not None else get_orchestration_lock_root(),
        mutation_domain_id=mutation_domain_id,
    )


def acquire_global_workflow_lock_async(
    *, timeout: float | None = None, lock_root: Path | None = None
) -> _AsyncFileLockContext:
    return _AsyncFileLockContext(
        scope="global_workflow",
        timeout=timeout,
        lock_root=lock_root if lock_root is not None else get_orchestration_lock_root(),
    )


def acquire_target_mutation_lock_async(
    mutation_domain_id: str,
    *,
    timeout: float | None = None,
    lock_root: Path | None = None,
) -> _AsyncFileLockContext:
    return _AsyncFileLockContext(
        scope="target_mutation",
        timeout=timeout,
        lock_root=lock_root if lock_root is not None else get_orchestration_lock_root(),
        mutation_domain_id=mutation_domain_id,
    )
