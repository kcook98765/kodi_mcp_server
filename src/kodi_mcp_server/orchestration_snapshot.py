"""Operation-owned deterministic repository snapshots for future staging.

Repository traversal and cleanup are descriptor-relative and no-follow. Each
operation owns a directory inode plus an exclusive lease lock for the full
snapshot lifetime. Identity is computed from the same held read descriptor that
future transport readers reopen.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

from kodi_mcp_server.orchestration_locking import acquire_global_workflow_lock

_SNAPSHOT_FILENAME = "repository-snapshot.zip"
_TEMP_FILENAME = ".repository-snapshot.tmp"
_OWNER_FILENAME = ".snapshot-owner.json"
_LEASE_FILENAME = "lease.lock"
_OWNER_KIND = "repository-snapshot-v1"
_OPERATION_PREFIX = "operation-"
_DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ALLOWED_OPERATION_NAMES = {
    _SNAPSHOT_FILENAME,
    _TEMP_FILENAME,
    _OWNER_FILENAME,
    _LEASE_FILENAME,
}


def default_snapshot_root() -> Path:
    from kodi_mcp_server.paths import PROJECT_ROOT

    return PROJECT_ROOT / "project" / "orchestration-snapshots"


def _owner_payload(operation_id: str) -> dict[str, str]:
    return {"kind": _OWNER_KIND, "operation_id": operation_id}


def _valid_operation_id(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _read_fd_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_named_regular_file(directory_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("snapshot operation entry must be a regular file")
        return _read_fd_all(fd)
    finally:
        os.close(fd)


def _marker_matches(operation_fd: int, operation_id: str) -> bool:
    try:
        raw = _read_named_regular_file(operation_fd, _OWNER_FILENAME)
        return json.loads(raw.decode("utf-8")) == _owner_payload(operation_id)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return False


def _entry_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISDIR(value.st_mode):
        return None
    return value.st_dev, value.st_ino


def _cleanup_operation_inode(
    *,
    root_fd: int,
    operation_fd: int,
    operation_name: str,
    operation_id: str,
    identity: tuple[int, int],
    lease_fd: int | None,
    require_marker: bool,
) -> None:
    """Clean known entries through held fds without following replacement paths."""

    try:
        current = os.fstat(operation_fd)
        if (current.st_dev, current.st_ino) != identity:
            return
        if require_marker and not _marker_matches(operation_fd, operation_id):
            return
        names = set(os.listdir(operation_fd))
        if not names.issubset(_ALLOWED_OPERATION_NAMES):
            return
        for name in (_SNAPSHOT_FILENAME, _TEMP_FILENAME, _OWNER_FILENAME, _LEASE_FILENAME):
            try:
                os.unlink(name, dir_fd=operation_fd)
            except FileNotFoundError:
                pass
    finally:
        if lease_fd is not None:
            try:
                fcntl.flock(lease_fd, fcntl.LOCK_UN)
            finally:
                os.close(lease_fd)
        # Never remove whatever now occupies the pathname unless it is the same
        # directory inode opened by this operation.
        if _entry_identity(root_fd, operation_name) == identity:
            try:
                os.rmdir(operation_name, dir_fd=root_fd)
            except OSError:
                pass
        os.close(operation_fd)
        os.close(root_fd)


def reap_stale_operation_snapshots(
    snapshot_root: Path,
    *,
    older_than_seconds: float = _DEFAULT_RETENTION_SECONDS,
    now: float | None = None,
) -> list[str]:
    """Reap only stale, marker-proven, inactive leased operation directories."""

    root = Path(snapshot_root)
    if not root.exists():
        return []
    cutoff = (time.time() if now is None else now) - older_than_seconds
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    removed: list[str] = []
    try:
        for operation_name in sorted(os.listdir(root_fd)):
            if not operation_name.startswith(_OPERATION_PREFIX):
                continue
            operation_id = operation_name[len(_OPERATION_PREFIX) :]
            if not _valid_operation_id(operation_id):
                continue
            try:
                operation_fd = os.open(
                    operation_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=root_fd,
                )
            except OSError:
                continue
            close_operation = True
            try:
                identity_stat = os.fstat(operation_fd)
                identity = (identity_stat.st_dev, identity_stat.st_ino)
                if identity_stat.st_mtime > cutoff:
                    continue
                names = set(os.listdir(operation_fd))
                if (
                    not names.issubset(_ALLOWED_OPERATION_NAMES)
                    or _LEASE_FILENAME not in names
                    or not _marker_matches(operation_fd, operation_id)
                ):
                    continue
                try:
                    lease_fd = os.open(
                        _LEASE_FILENAME,
                        os.O_RDWR | os.O_NOFOLLOW,
                        dir_fd=operation_fd,
                    )
                except OSError:
                    continue
                try:
                    fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(lease_fd)
                    continue
                child_root_fd = os.dup(root_fd)
                close_operation = False
                _cleanup_operation_inode(
                    root_fd=child_root_fd,
                    operation_fd=operation_fd,
                    operation_name=operation_name,
                    operation_id=operation_id,
                    identity=identity,
                    lease_fd=lease_fd,
                    require_marker=True,
                )
                if _entry_identity(root_fd, operation_name) is None:
                    removed.append(operation_id)
            finally:
                if close_operation:
                    os.close(operation_fd)
    finally:
        os.close(root_fd)
    return removed


def _validate_archive_member_name(relative: str) -> str:
    if (
        not isinstance(relative, str)
        or not relative
        or "\x00" in relative
        or "\\" in relative
        or relative.startswith("/")
        or (len(relative) >= 2 and relative[0].isalpha() and relative[1] == ":")
    ):
        raise ValueError("unsafe snapshot member path")
    components = relative.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("unsafe snapshot member path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or tuple(components) != pure.parts:
        raise ValueError("unsafe snapshot member path")
    return relative


def _validate_relative_member(relative: str) -> tuple[str, ...]:
    return tuple(_validate_archive_member_name(relative).split("/"))


def _enumerate_members(directory_fd: int, prefix: tuple[str, ...] = ()) -> list[str]:
    members: list[str] = []
    for name in sorted(os.listdir(directory_fd)):
        if name in {"", ".", ".."} or "/" in name:
            raise ValueError("unsafe snapshot member path")
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        relative_parts = prefix + (name,)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("snapshot source must not contain symlinks")
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                members.extend(_enumerate_members(child_fd, relative_parts))
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            members.append(_validate_archive_member_name("/".join(relative_parts)))
        else:
            raise ValueError("snapshot source member must be a regular file")
    return members


def _read_regular_member(root_fd: int, relative: str) -> bytes:
    parts = _validate_relative_member(relative)
    directory_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("snapshot source member must be a regular file")
            return _read_fd_all(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("snapshot source changed or contains a symlink") from exc
    finally:
        os.close(directory_fd)


def _write_deterministic_zip(
    source_dir: Path,
    destination: Path | BinaryIO,
    *,
    _before_member_open: Callable[[str], None] | None = None,
) -> None:
    root_fd = os.open(source_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        members = _enumerate_members(root_fd)
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for archive_name in members:
                _validate_archive_member_name(archive_name)
                if _before_member_open is not None:
                    _before_member_open(archive_name)
                data = _read_regular_member(root_fd, archive_name)
                info = zipfile.ZipInfo(archive_name, date_time=_FIXED_ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(
                    info,
                    data,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
    finally:
        os.close(root_fd)


def _descriptor_identity(fileobj: BinaryIO) -> tuple[int, str]:
    fileobj.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = fileobj.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    if os.fstat(fileobj.fileno()).st_size != size:
        raise OSError("snapshot descriptor size changed during identity calculation")
    fileobj.seek(0)
    return size, digest.hexdigest()


@dataclass(frozen=True)
class RepositorySnapshot:
    operation_id: str
    filename: str
    size_bytes: int
    sha256: str
    created_at: int
    internal_path: Path = field(repr=False, compare=False)
    _descriptor: BinaryIO = field(repr=False, compare=False)
    _root_fd: int = field(repr=False, compare=False)
    _operation_fd: int = field(repr=False, compare=False)
    _operation_name: str = field(repr=False, compare=False)
    _operation_identity: tuple[int, int] = field(repr=False, compare=False)
    _lease_fd: int = field(repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def public_identity(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
        }

    def open_reader(self) -> BinaryIO:
        if self._closed:
            raise ValueError("repository snapshot is closed")
        # Opening procfs for the held inode creates an independent open-file
        # description and therefore an independent seek offset.
        duplicate = os.open(f"/proc/self/fd/{self._descriptor.fileno()}", os.O_RDONLY)
        return os.fdopen(duplicate, "rb")

    def close(self) -> None:
        if self._closed:
            return
        object.__setattr__(self, "_closed", True)
        self._descriptor.close()
        _cleanup_operation_inode(
            root_fd=self._root_fd,
            operation_fd=self._operation_fd,
            operation_name=self._operation_name,
            operation_id=self.operation_id,
            identity=self._operation_identity,
            lease_fd=self._lease_fd,
            require_marker=True,
        )

    def __enter__(self) -> "RepositorySnapshot":
        if self._closed:
            raise ValueError("repository snapshot is closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _write_operation_file(operation_fd: int, name: str, data: bytes, mode: int) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=operation_fd,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("failed to write snapshot operation file")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def create_repository_snapshot(
    *,
    source_dir: Path,
    snapshot_root: Path | None = None,
    lock_root: Path | None = None,
    lock_timeout: float | None = None,
    _on_lock_acquired: Callable[[], None] | None = None,
    _before_member_open: Callable[[str], None] | None = None,
    _after_operation_open: Callable[[Path], None] | None = None,
) -> RepositorySnapshot:
    source = Path(source_dir)
    if not source.is_dir() or source.is_symlink():
        raise FileNotFoundError("repository snapshot source directory not found")
    root = Path(snapshot_root or default_snapshot_root())
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    reap_stale_operation_snapshots(root)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    operation_id = str(uuid.uuid4())
    operation_name = f"{_OPERATION_PREFIX}{operation_id}"
    os.mkdir(operation_name, mode=0o700, dir_fd=root_fd)
    operation_fd = os.open(
        operation_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=root_fd,
    )
    identity_stat = os.fstat(operation_fd)
    identity = (identity_stat.st_dev, identity_stat.st_ino)
    lease_fd: int | None = None
    descriptor: BinaryIO | None = None
    operation_path = root / operation_name
    try:
        _write_operation_file(
            operation_fd,
            _OWNER_FILENAME,
            json.dumps(_owner_payload(operation_id), sort_keys=True).encode("utf-8"),
            0o600,
        )
        lease_fd = os.open(
            _LEASE_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=operation_fd,
        )
        fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _after_operation_open is not None:
            _after_operation_open(operation_path)
        with acquire_global_workflow_lock(timeout=lock_timeout, lock_root=lock_root):
            if _on_lock_acquired is not None:
                _on_lock_acquired()
            temporary_fd = os.open(
                _TEMP_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=operation_fd,
            )
            with os.fdopen(temporary_fd, "w+b") as temporary_stream:
                _write_deterministic_zip(
                    source,
                    temporary_stream,
                    _before_member_open=_before_member_open,
                )
                temporary_stream.flush()
                os.fsync(temporary_stream.fileno())
            os.replace(
                _TEMP_FILENAME,
                _SNAPSHOT_FILENAME,
                src_dir_fd=operation_fd,
                dst_dir_fd=operation_fd,
            )
            os.chmod(
                _SNAPSHOT_FILENAME,
                0o444,
                dir_fd=operation_fd,
                follow_symlinks=False,
            )
            final_fd = os.open(
                _SNAPSHOT_FILENAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=operation_fd
            )
            descriptor = os.fdopen(final_fd, "rb")
            size_bytes, sha256 = _descriptor_identity(descriptor)
            os.fsync(operation_fd)
        return RepositorySnapshot(
            operation_id=operation_id,
            filename=_SNAPSHOT_FILENAME,
            size_bytes=size_bytes,
            sha256=sha256,
            created_at=int(time.time()),
            internal_path=operation_path / _SNAPSHOT_FILENAME,
            _descriptor=descriptor,
            _root_fd=root_fd,
            _operation_fd=operation_fd,
            _operation_name=operation_name,
            _operation_identity=identity,
            _lease_fd=lease_fd,
        )
    except BaseException:
        if descriptor is not None:
            descriptor.close()
        _cleanup_operation_inode(
            root_fd=root_fd,
            operation_fd=operation_fd,
            operation_name=operation_name,
            operation_id=operation_id,
            identity=identity,
            lease_fd=lease_fd,
            require_marker=False,
        )
        raise
