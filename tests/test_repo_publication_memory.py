import hashlib
import os
import stat
import tracemalloc
import zipfile
from pathlib import Path

import pytest

from kodi_mcp_server.repo_ops import RepoPublisher, RepositoryPublicationError

_FIXTURE_BYTES = 16 * 1024 * 1024


def _write_large_valid_addon_zip(path: Path, total_bytes: int = _FIXTURE_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "plugin.test/addon.xml",
            '<addon id="plugin.test" name="Test" version="2.0.0" provider-name="test"/>',
        )
        with archive.open("plugin.test/payload.bin", "w") as payload:
            remaining = total_bytes
            chunk = b"x" * (64 * 1024)
            while remaining:
                part = chunk[: min(len(chunk), remaining)]
                payload.write(part)
                remaining -= len(part)


def _write_large_prior(path: Path, total_bytes: int = _FIXTURE_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        chunk = b"p" * (64 * 1024)
        remaining = total_bytes
        while remaining:
            part = chunk[: min(len(chunk), remaining)]
            stream.write(part)
            remaining -= len(part)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(repo_root: Path) -> tuple[Path, Path]:
    dev = repo_root / "dev-repo"
    dev.mkdir(parents=True, exist_ok=True)
    xml = dev / "addons.xml"
    xml_bytes = b'<addons><addon id="plugin.old" version="1.0.0"/></addons>'
    xml.write_bytes(xml_bytes)
    md5 = dev / "addons.xml.md5"
    md5.write_text(
        f"{hashlib.md5(xml_bytes).hexdigest()}  addons.xml\n", encoding="utf-8"
    )
    return xml, md5


class _GuardedReader:
    def __init__(self, wrapped, path: Path, reads: dict[Path, list[int]], maximum: int):
        self._wrapped = wrapped
        self._path = path
        self._reads = reads
        self._maximum = maximum

    def read(self, size=-1):
        assert 0 <= size <= self._maximum, f"unbounded archive read requested: {size}"
        self._reads.setdefault(self._path, []).append(size)
        return self._wrapped.read(size)

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, *args):
        return self._wrapped.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def _guard_archive_reads(monkeypatch, archive_paths: set[Path], maximum: int):
    reads: dict[Path, list[int]] = {}
    original_open = Path.open
    original_read_bytes = Path.read_bytes

    def guarded_open(self, mode="r", *args, **kwargs):
        stream = original_open(self, mode, *args, **kwargs)
        if self in archive_paths and mode == "rb":
            return _GuardedReader(stream, self, reads, maximum)
        return stream

    def guarded_read_bytes(self):
        if self in archive_paths or self.suffix == ".zip":
            raise AssertionError("whole-file archive read is forbidden")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    return reads


def _publish(repo_root: Path, incoming: Path):
    return RepoPublisher(repo_root).publish_addon(
        addon_zip_path=str(incoming),
        addon_id="plugin.test",
        addon_name="Test",
        addon_version="2.0.0",
    )


def test_large_incoming_zip_is_streamed_with_bounded_reads_and_exact_sha(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.repo_ops as module

    repo_root = tmp_path / "repo"
    _metadata(repo_root)
    incoming = tmp_path / "incoming" / "plugin.test-2.0.0.zip"
    _write_large_valid_addon_zip(incoming)
    expected_sha = _sha256(incoming)
    chunk_size = module.PUBLICATION_COPY_CHUNK_SIZE
    reads = _guard_archive_reads(monkeypatch, {incoming}, chunk_size)

    tracemalloc.start()
    _publish(repo_root, incoming)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    final = repo_root / "dev-repo" / "zips" / "plugin.test" / incoming.name
    assert _sha256(final) == expected_sha
    assert reads[incoming]
    assert max(reads[incoming]) <= chunk_size
    assert peak < _FIXTURE_BYTES // 2
    print(f"bounded_publication_peak_bytes={peak}")
    rollback_root = repo_root / ".publication-rollbacks"
    assert not rollback_root.exists() or list(rollback_root.iterdir()) == []


def test_large_prior_zip_uses_bounded_disk_backup_and_restores_sha_mode_and_metadata(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.repo_ops as module

    repo_root = tmp_path / "repo"
    xml, md5 = _metadata(repo_root)
    final = repo_root / "dev-repo" / "zips" / "plugin.test" / "plugin.test-2.0.0.zip"
    _write_large_prior(final)
    final.chmod(0o640)
    prior_sha = _sha256(final)
    prior_metadata = (xml.read_bytes(), md5.read_bytes())
    incoming = tmp_path / "incoming" / "plugin.test-2.0.0.zip"
    _write_large_valid_addon_zip(incoming, total_bytes=2 * 1024 * 1024)
    unrelated_root = repo_root / ".publication-rollbacks"
    unrelated_root.mkdir()
    unrelated = unrelated_root / "unrelated.keep"
    unrelated.write_bytes(b"keep")
    reads = _guard_archive_reads(
        monkeypatch, {incoming, final}, module.PUBLICATION_COPY_CHUNK_SIZE
    )
    original_replace = module.os.replace
    calls = 0

    def fail_md5_replace(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("md5 replace failed")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_md5_replace)
    with pytest.raises(OSError, match="md5 replace failed"):
        _publish(repo_root, incoming)

    assert _sha256(final) == prior_sha
    assert stat.S_IMODE(final.stat().st_mode) == 0o640
    assert (xml.read_bytes(), md5.read_bytes()) == prior_metadata
    assert reads[incoming] and reads[final]
    assert max(reads[incoming] + reads[final]) <= module.PUBLICATION_COPY_CHUNK_SIZE
    assert unrelated.read_bytes() == b"keep"
    assert [entry for entry in unrelated_root.iterdir() if entry != unrelated] == []


def test_rollback_failure_remains_explicit_and_pathless(tmp_path, monkeypatch):
    import kodi_mcp_server.repo_ops as module

    repo_root = tmp_path / "repo"
    _metadata(repo_root)
    final = repo_root / "dev-repo" / "zips" / "plugin.test" / "plugin.test-2.0.0.zip"
    _write_large_prior(final, total_bytes=1024 * 1024)
    incoming = tmp_path / "incoming" / "plugin.test-2.0.0.zip"
    _write_large_valid_addon_zip(incoming, total_bytes=1024 * 1024)
    original_replace = module.os.replace
    calls = 0

    def fail_publication_and_zip_restore(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls in {3, 5}:
            raise OSError("injected")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_publication_and_zip_restore)
    with pytest.raises(RepositoryPublicationError) as exc_info:
        _publish(repo_root, incoming)

    assert exc_info.value.error_code == "REPOSITORY_PUBLICATION_ROLLBACK_FAILED"
    assert "/" not in str(exc_info.value)
