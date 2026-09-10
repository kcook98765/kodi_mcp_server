import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kodi_mcp_server.repo_ops import RepoPublisher


def _addon_zip(path: Path) -> None:
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "plugin.test/addon.xml",
            '<addon id="plugin.test" name="Test" version="2.0.0" provider-name="test"/>',
        )


def test_metadata_http_reader_waits_for_publisher_and_reads_complete_final(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.repo_ops as repo_ops
    import kodi_mcp_server.repo_server as repo_server

    repo_root = tmp_path / "repo"
    dev_repo = repo_root / "dev-repo"
    dev_repo.mkdir(parents=True)
    (dev_repo / "addons.xml").write_text("<addons></addons>", encoding="utf-8")
    (dev_repo / "addons.xml.md5").write_text("old  addons.xml\n", encoding="utf-8")
    addon_zip = tmp_path / "build" / "plugin.test-2.0.0.zip"
    _addon_zip(addon_zip)

    monkeypatch.setattr(repo_server, "REPO_ROOT", repo_root)
    prepared = threading.Event()
    release = threading.Event()
    reader_started = threading.Event()
    reader_done = threading.Event()
    original = repo_ops._write_fsynced_temp
    calls = 0

    def pause(path, data):
        nonlocal calls
        result = original(path, data)
        calls += 1
        if calls == 1:
            prepared.set()
            release.wait(5)
        return result

    monkeypatch.setattr(repo_ops, "_write_fsynced_temp", pause)
    app = FastAPI()
    repo_server.mount_repo_static(app)
    client = TestClient(app)

    def publish():
        RepoPublisher(repo_root).publish_addon(
            addon_zip_path=str(addon_zip),
            addon_id="plugin.test",
            addon_name="Test",
            addon_version="2.0.0",
        )

    response_holder = {}

    def read():
        reader_started.set()
        response_holder["response"] = client.get("/repo/content/addons.xml")
        reader_done.set()

    publisher = threading.Thread(target=publish)
    publisher.start()
    assert prepared.wait(5)
    reader = threading.Thread(target=read)
    reader.start()
    assert reader_started.wait(2)
    assert reader_done.wait(0.2) is False
    release.set()
    publisher.join(5)
    reader.join(5)

    response = response_holder["response"]
    assert response.status_code == 200
    assert b"plugin.test" in response.content
    assert response.content == (dev_repo / "addons.xml").read_bytes()
