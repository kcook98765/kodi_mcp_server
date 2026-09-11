import hashlib
import json
import multiprocessing
import os
import time
import zipfile
from pathlib import Path

import pytest

from kodi_mcp_server.orchestration_locking import acquire_global_workflow_lock
from kodi_mcp_server.orchestration_snapshot import (
    create_repository_snapshot,
    reap_stale_operation_snapshots,
)


def _build_waiting_snapshot(
    source: str,
    snapshot_root: str,
    lock_root: str,
    ready,
    proceed,
    result_queue,
) -> None:
    def on_lock_acquired() -> None:
        ready.set()
        proceed.wait(5)

    with create_repository_snapshot(
        source_dir=Path(source),
        snapshot_root=Path(snapshot_root),
        lock_root=Path(lock_root),
        _on_lock_acquired=on_lock_acquired,
    ) as snapshot:
        with snapshot.open_reader() as reader, zipfile.ZipFile(reader) as archive:
            result_queue.put(
                {
                    name: archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                }
            )


def _mutate_after_ready(source: str, lock_root: str, ready, done) -> None:
    ready.wait(5)
    with acquire_global_workflow_lock(lock_root=Path(lock_root), timeout=5):
        root = Path(source)
        (root / "addons.xml").write_text("new-metadata", encoding="utf-8")
        (root / "zips" / "addon.zip").write_text("new-artifact", encoding="utf-8")
    done.set()


def _tree(root: Path, *, reverse: bool, timestamp: int, modes: tuple[int, int]) -> None:
    paths = [
        ("addons.xml", b"<addons/>\n"),
        ("zips/plugin.test/plugin.test-1.0.0.zip", b"artifact-bytes"),
    ]
    if reverse:
        paths.reverse()
    for index, (relative, data) in enumerate(paths):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (timestamp + index, timestamp + index))
        path.chmod(modes[index])


def test_snapshot_is_byte_deterministic_across_mtime_creation_order_and_modes(tmp_path):
    first_tree = tmp_path / "first"
    second_tree = tmp_path / "second"
    _tree(first_tree, reverse=False, timestamp=1_600_000_000, modes=(0o600, 0o755))
    _tree(second_tree, reverse=True, timestamp=1_700_000_000, modes=(0o644, 0o640))

    identities = []
    payloads = []
    for index, source in enumerate((first_tree, second_tree, first_tree, second_tree)):
        with create_repository_snapshot(
            source_dir=source,
            snapshot_root=tmp_path / f"snapshots-{index}",
            lock_root=tmp_path / "locks",
        ) as snapshot:
            with snapshot.open_reader() as reader:
                payload = reader.read()
            payloads.append(payload)
            identities.append((snapshot.size_bytes, snapshot.sha256))

    assert len(set(payloads)) == 1
    assert len(set(identities)) == 1


def test_snapshot_identity_is_computed_from_exact_held_descriptor_bytes(tmp_path):
    source = tmp_path / "repo"
    _tree(source, reverse=False, timestamp=1_600_000_000, modes=(0o600, 0o755))

    with create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
    ) as snapshot:
        with snapshot.open_reader() as reader:
            frozen = reader.read()
        assert snapshot.size_bytes == len(frozen)
        assert snapshot.sha256 == hashlib.sha256(frozen).hexdigest()
        assert snapshot.public_identity() == {
            "operation_id": snapshot.operation_id,
            "filename": "repository-snapshot.zip",
            "size_bytes": len(frozen),
            "sha256": hashlib.sha256(frozen).hexdigest(),
            "created_at": snapshot.created_at,
        }
        assert str(tmp_path) not in json.dumps(snapshot.public_identity())


def test_canonical_path_replacement_cannot_change_frozen_snapshot_or_descriptor(tmp_path):
    source = tmp_path / "repo"
    _tree(source, reverse=False, timestamp=1_600_000_000, modes=(0o644, 0o644))

    with create_repository_snapshot(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        lock_root=tmp_path / "locks",
    ) as snapshot:
        with snapshot.open_reader() as reader:
            before = reader.read()
        (source / "addons.xml").write_text("replacement", encoding="utf-8")
        canonical_zip = tmp_path / "dev-repo.zip"
        canonical_zip.write_bytes(b"replacement-canonical-zip")
        with snapshot.open_reader() as reader:
            after = reader.read()

        assert after == before
        assert hashlib.sha256(after).hexdigest() == snapshot.sha256
        assert snapshot.size_bytes == len(after)


def test_snapshot_cleanup_is_owned_and_unrelated_files_are_untouched(tmp_path):
    source = tmp_path / "repo"
    _tree(source, reverse=False, timestamp=1_600_000_000, modes=(0o644, 0o644))
    snapshot_root = tmp_path / "snapshots"
    unrelated = snapshot_root / "unrelated.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("keep", encoding="utf-8")

    with create_repository_snapshot(
        source_dir=source,
        snapshot_root=snapshot_root,
        lock_root=tmp_path / "locks",
    ) as snapshot:
        owned_dir = snapshot.internal_path.parent
        assert snapshot.internal_path.is_file()

    assert not owned_dir.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_snapshot_builder_cleans_its_operation_directory_after_handled_error(
    tmp_path, monkeypatch
):
    source = tmp_path / "repo"
    _tree(source, reverse=False, timestamp=1_600_000_000, modes=(0o644, 0o644))
    snapshot_root = tmp_path / "snapshots"

    import kodi_mcp_server.orchestration_snapshot as snapshots

    def fail_write(*args, **kwargs):
        raise OSError("injected snapshot failure")

    monkeypatch.setattr(snapshots, "_write_deterministic_zip", fail_write)
    with pytest.raises(OSError, match="injected snapshot failure"):
        create_repository_snapshot(
            source_dir=source,
            snapshot_root=snapshot_root,
            lock_root=tmp_path / "locks",
        )

    assert list(snapshot_root.iterdir()) == []


def test_crash_leftover_reaper_is_conservative_and_deterministic(tmp_path):
    root = tmp_path / "snapshots"
    root.mkdir()
    operation_id = "11111111-1111-4111-8111-111111111111"
    owned = root / f"operation-{operation_id}"
    owned.mkdir()
    (owned / ".snapshot-owner.json").write_text(
        json.dumps({"kind": "repository-snapshot-v1", "operation_id": operation_id}),
        encoding="utf-8",
    )
    (owned / "repository-snapshot.zip").write_bytes(b"stale")
    (owned / "lease.lock").write_bytes(b"")
    old = time.time() - 10_000
    os.utime(owned, (old, old))

    unknown_dir = root / "operation-22222222-2222-4222-8222-222222222222"
    unknown_dir.mkdir()
    (unknown_dir / "unknown.txt").write_text("keep", encoding="utf-8")
    unrelated = root / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    removed = reap_stale_operation_snapshots(root, older_than_seconds=3600, now=time.time())

    assert removed == [operation_id]
    assert not owned.exists()
    assert (unknown_dir / "unknown.txt").read_text(encoding="utf-8") == "keep"
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_source_writer_blocks_until_snapshot_is_frozen_without_mixed_archive(tmp_path):
    source = tmp_path / "repo"
    (source / "zips").mkdir(parents=True)
    (source / "addons.xml").write_text("old-metadata", encoding="utf-8")
    (source / "zips" / "addon.zip").write_text("old-artifact", encoding="utf-8")

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    proceed = ctx.Event()
    writer_done = ctx.Event()
    results = ctx.Queue()
    builder = ctx.Process(
        target=_build_waiting_snapshot,
        args=(
            str(source),
            str(tmp_path / "snapshots"),
            str(tmp_path / "locks"),
            ready,
            proceed,
            results,
        ),
    )
    writer = ctx.Process(
        target=_mutate_after_ready,
        args=(str(source), str(tmp_path / "locks"), ready, writer_done),
    )
    builder.start()
    writer.start()
    assert ready.wait(5)
    assert writer_done.wait(0.2) is False
    proceed.set()
    builder.join(5)
    writer.join(5)

    assert builder.exitcode == writer.exitcode == 0
    assert results.get(timeout=1) == {
        "addons.xml": "old-metadata",
        "zips/addon.zip": "old-artifact",
    }
    assert writer_done.is_set()
    assert (source / "addons.xml").read_text(encoding="utf-8") == "new-metadata"
    assert (source / "zips" / "addon.zip").read_text(encoding="utf-8") == "new-artifact"
