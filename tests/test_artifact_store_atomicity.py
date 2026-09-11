import io
import json
import threading
from pathlib import Path

import pytest

from kodi_mcp_server.artifact_store import ArtifactConflictError, ArtifactStore


def test_duplicate_explicit_artifact_id_is_rejected_before_destination_mutation(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    original = store.register_bytes(
        data=b"original-bytes", filename="first.zip", artifact_id="same-id"
    )
    index_before = store.index_path.read_bytes()
    bytes_before = Path(original.path).read_bytes()

    with pytest.raises(ArtifactConflictError) as exc_info:
        store.register_filelike(
            fileobj=io.BytesIO(b"replacement"),
            filename="second.zip",
            artifact_id="same-id",
        )

    assert exc_info.value.error_code == "ARTIFACT_ID_CONFLICT"
    assert Path(original.path).read_bytes() == bytes_before
    assert store.index_path.read_bytes() == index_before
    assert store.get("same-id") == original


def test_new_artifact_final_and_index_remain_invisible_until_stream_complete(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    started = threading.Event()
    release = threading.Event()
    done = threading.Event()
    result = {}

    class SlowReader:
        calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                started.set()
                release.wait(5)
                return b"partial-"
            if self.calls == 2:
                return b"complete"
            return b""

    def register():
        result["record"] = store.register_filelike(
            fileobj=SlowReader(), filename="upload.zip", artifact_id="new-id"
        )
        done.set()

    thread = threading.Thread(target=register)
    thread.start()
    assert started.wait(5)
    final_path = tmp_path / "artifacts" / "new-id.zip"
    assert not final_path.exists()
    assert store.get("new-id") is None
    if store.index_path.exists():
        assert "new-id" not in json.loads(store.index_path.read_text())["artifacts"]

    release.set()
    assert done.wait(5)
    thread.join(5)
    assert final_path.read_bytes() == b"partial-complete"
    assert store.get("new-id") == result["record"]
    assert list((tmp_path / "artifacts").glob(".artifact-*")) == []


def test_midstream_failure_leaves_no_final_or_index_and_cleans_temp(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    existing = store.register_bytes(
        data=b"existing", filename="existing.zip", artifact_id="existing"
    )
    existing_index = store.index_path.read_bytes()

    class FailingReader:
        calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("injected stream failure")

    with pytest.raises(OSError, match="injected stream failure"):
        store.register_filelike(
            fileobj=FailingReader(), filename="failed.zip", artifact_id="failed"
        )

    assert not (tmp_path / "artifacts" / "failed.zip").exists()
    assert store.get("failed") is None
    assert Path(existing.path).read_bytes() == b"existing"
    assert store.index_path.read_bytes() == existing_index
    assert list((tmp_path / "artifacts").glob(".artifact-*")) == []
