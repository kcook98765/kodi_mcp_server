import fcntl
import json
import os
import time
import zipfile
from pathlib import Path

import pytest

from kodi_mcp_server.orchestration_snapshot import (
    create_repository_snapshot,
    reap_stale_operation_snapshots,
)


def _source(root: Path) -> Path:
    root.mkdir()
    (root / "file.txt").write_text("inside", encoding="utf-8")
    return root


def test_file_replaced_by_external_symlink_after_enumeration_fails_closed(tmp_path):
    source = _source(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")

    def replace(relative):
        if relative == "file.txt":
            (source / relative).unlink()
            (source / relative).symlink_to(outside)

    with pytest.raises((OSError, ValueError), match="snapshot source|symlink|regular"):
        create_repository_snapshot(
            source_dir=source,
            snapshot_root=tmp_path / "snapshots",
            lock_root=tmp_path / "locks",
            _before_member_open=replace,
        )


def test_ancestor_replaced_by_external_symlink_after_enumeration_fails_closed(tmp_path):
    source = tmp_path / "repo"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "file.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("outside-secret", encoding="utf-8")

    def replace(relative):
        if relative == "nested/file.txt":
            (source / "nested").rename(source / "nested-old")
            (source / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises((OSError, ValueError), match="snapshot source|symlink|regular"):
        create_repository_snapshot(
            source_dir=source,
            snapshot_root=tmp_path / "snapshots",
            lock_root=tmp_path / "locks",
            _before_member_open=replace,
        )


def test_member_replaced_by_fifo_is_rejected_without_blocking(tmp_path):
    source = _source(tmp_path / "repo")

    def replace(relative):
        if relative == "file.txt":
            (source / relative).unlink()
            os.mkfifo(source / relative)

    started = time.monotonic()
    with pytest.raises(ValueError, match="regular file"):
        create_repository_snapshot(
            source_dir=source,
            snapshot_root=tmp_path / "snapshots",
            lock_root=tmp_path / "locks",
            _before_member_open=replace,
        )
    assert time.monotonic() - started < 2


def test_initial_symlink_outside_is_rejected(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "link.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        create_repository_snapshot(
            source_dir=source,
            snapshot_root=tmp_path / "snapshots",
            lock_root=tmp_path / "locks",
        )


def test_descriptor_relative_reader_rejects_parent_escape(tmp_path):
    import kodi_mcp_server.orchestration_snapshot as module

    source = _source(tmp_path / "repo")
    root_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="unsafe snapshot member"):
            module._read_regular_member(root_fd, "../outside.txt")
    finally:
        os.close(root_fd)


@pytest.mark.parametrize(
    "unsafe",
    [
        "..\\escape.txt",
        "dir\\..\\escape.txt",
        "C:\\drive.txt",
        "C:drive.txt",
        "\\\\server\\share.txt",
        "/absolute.txt",
        "../escape.txt",
        "dir/../escape.txt",
        "./file.txt",
        "name\\file.txt",
        "nul\x00name.txt",
        "dir//file.txt",
    ],
)
def test_unsafe_archive_member_names_are_rejected(unsafe):
    import kodi_mcp_server.orchestration_snapshot as module

    with pytest.raises(ValueError, match="unsafe snapshot member"):
        module._validate_archive_member_name(unsafe)


@pytest.mark.parametrize(
    "safe",
    [
        "addons.xml",
        "plugin.video.example/addon.xml",
        "resources/media/icon.png",
    ],
)
def test_safe_nested_posix_archive_member_names_are_allowed(safe):
    import kodi_mcp_server.orchestration_snapshot as module

    assert module._validate_archive_member_name(safe) == safe


def test_snapshot_output_remains_bound_to_operation_fd_after_path_replacement(tmp_path):
    source = _source(tmp_path / "repo")
    external = tmp_path / "external"
    external.mkdir()
    state = {}

    def replace(operation_path):
        renamed = operation_path.with_name(operation_path.name + "-renamed")
        operation_path.rename(renamed)
        operation_path.symlink_to(external, target_is_directory=True)
        state["renamed"] = renamed

    snapshot = create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
        _after_operation_open=replace,
    )
    try:
        assert list(external.iterdir()) == []
        with snapshot.open_reader() as reader, zipfile.ZipFile(reader) as archive:
            assert archive.read("file.txt") == b"inside"
        assert (state["renamed"] / "repository-snapshot.zip").is_file()
    finally:
        snapshot.close()

    assert list(external.iterdir()) == []


def test_operation_cleanup_does_not_follow_replaced_directory_symlink(tmp_path):
    source = _source(tmp_path / "repo")
    external = tmp_path / "external"
    external.mkdir()
    external_marker = external / ".snapshot-owner.json"
    external_snapshot = external / "repository-snapshot.zip"
    external_lease = external / "lease.lock"
    snapshot = create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
    )
    external_marker.write_text(
        json.dumps({"kind": "repository-snapshot-v1", "operation_id": snapshot.operation_id}),
        encoding="utf-8",
    )
    external_snapshot.write_bytes(b"external")
    external_lease.write_bytes(b"external-lease")
    original_dir = snapshot.internal_path.parent
    renamed = original_dir.with_name(original_dir.name + "-renamed")
    original_dir.rename(renamed)
    original_dir.symlink_to(external, target_is_directory=True)

    snapshot.close()

    assert external_marker.is_file()
    assert external_snapshot.read_bytes() == b"external"
    assert external_lease.read_bytes() == b"external-lease"
    assert original_dir.is_symlink()


def _raw_owned_operation(root: Path, operation_id: str) -> tuple[Path, int]:
    operation = root / f"operation-{operation_id}"
    operation.mkdir(parents=True)
    (operation / ".snapshot-owner.json").write_text(
        json.dumps({"kind": "repository-snapshot-v1", "operation_id": operation_id}),
        encoding="utf-8",
    )
    (operation / "repository-snapshot.zip").write_bytes(b"stale")
    lease = os.open(operation / "lease.lock", os.O_RDWR | os.O_CREAT, 0o600)
    old = time.time() - 10_000
    os.utime(operation, (old, old))
    return operation, lease


def test_reaper_skips_active_stale_lease_then_reaps_after_release(tmp_path):
    root = tmp_path / "snapshots"
    operation_id = "11111111-1111-4111-8111-111111111111"
    operation, lease = _raw_owned_operation(root, operation_id)
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)

    assert reap_stale_operation_snapshots(root, older_than_seconds=1) == []
    assert operation.is_dir()

    fcntl.flock(lease, fcntl.LOCK_UN)
    os.close(lease)
    assert reap_stale_operation_snapshots(root, older_than_seconds=1) == [operation_id]
    assert not operation.exists()


def test_active_snapshot_lease_prevents_reap_and_reader_remains_valid(tmp_path):
    source = _source(tmp_path / "repo")
    snapshot = create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
    )
    operation = snapshot.internal_path.parent
    old = time.time() - 100_000
    os.utime(operation, (old, old))

    assert reap_stale_operation_snapshots(
        tmp_path / "snapshots", older_than_seconds=1
    ) == []
    with snapshot.open_reader() as reader, zipfile.ZipFile(reader) as archive:
        assert archive.read("file.txt") == b"inside"
    snapshot.close()


def test_snapshot_is_readonly_and_multiple_readers_have_independent_offsets(tmp_path):
    source = _source(tmp_path / "repo")
    snapshot = create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
    )
    first = snapshot.open_reader()
    second = snapshot.open_reader()
    try:
        assert first.read(8) == second.read(8)
        with pytest.raises(PermissionError):
            snapshot.internal_path.open("wb")
        snapshot.close()
        assert first.read() == second.read()
    finally:
        first.close()
        second.close()


def test_open_reader_proc_failure_is_explicit_and_parent_snapshot_survives(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.orchestration_snapshot as module

    source = _source(tmp_path / "repo")
    with create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
    ) as snapshot:
        original = module.os.open

        def fail_proc(path, *args, **kwargs):
            if str(path).startswith("/proc/self/fd/"):
                raise OSError("proc fd unavailable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(module.os, "open", fail_proc)
        with pytest.raises(OSError, match="proc fd unavailable"):
            snapshot.open_reader()
        monkeypatch.setattr(module.os, "open", original)
        with snapshot.open_reader() as reader:
            assert reader.read()
