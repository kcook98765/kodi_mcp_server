from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    # Minimal app: repo routes + tools routes.
    from kodi_mcp_server.repo_app import configure_repo_app
    from kodi_mcp_server.mcp_app import configure_mcp_app

    app = FastAPI()
    configure_repo_app(app)
    configure_mcp_app(app)
    return app


def test_publish_artifact_flow(tmp_path: Path, monkeypatch):
    """End-to-end local test for agent-safe publish flow.

    Validates:
    - artifact_id -> publish into repo/dev-repo
    - addons.xml gains an addon entry
    - zip is served under /repo/content/zips/<addon_id>/<addon_id>-<version>.zip
    """

    # Force the server to use an isolated repo root.
    # NOTE: config.REPO_ROOT is bound at import time, so we patch env and reload.
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPO_BASE_URL", "http://testserver")
    monkeypatch.setenv("KODI_JSONRPC_URL", "http://localhost:8080/jsonrpc")
    monkeypatch.setenv("KODI_BRIDGE_BASE_URL", "http://localhost:8765")

    # Patch AUTHORITATIVE_REPO_ROOT to tmp repo by patching paths module constant.
    # This is the narrowest way to keep the test local without refactoring config.
    import kodi_mcp_server.paths as paths
    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)

    # Reload config + repo modules so REPO_ROOT and mounts use the tmp repo.
    import importlib
    import kodi_mcp_server.config as config
    import kodi_mcp_server.repo_server as repo_server

    importlib.reload(config)
    importlib.reload(repo_server)

    # Ensure dev-repo baseline files exist.
    dev_repo = repo_root / "dev-repo"
    dev_repo.mkdir(parents=True, exist_ok=True)
    (dev_repo / "addons.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n</addons>\n',
        encoding="utf-8",
    )

    # Create a tiny fake addon zip (content isn't validated by RepoPublisher).
    fake_zip = tmp_path / "input.zip"
    fake_zip.write_bytes(b"PK\x03\x04")

    # Register into artifact store.
    from kodi_mcp_server.artifact_store import ArtifactStore

    store = ArtifactStore(root_dir=tmp_path / "artifacts")
    record = store.register_existing_file(file_path=fake_zip, addon_id="script.kodi_mcp_test", version="0.0.1")

    app = _make_app()
    client = TestClient(app)

    resp = client.post(
        "/tools/repo/publish_artifact",
        json={
            "artifact_id": record.artifact_id,
            "addon_id": "script.kodi_mcp_test",
            "addon_name": "Kodi MCP Test Script",
            "addon_version": "0.0.1",
            "provider_name": "kodi_mcp",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("error") is None

    # Server-side validation
    addons_xml = (dev_repo / "addons.xml").read_text(encoding="utf-8")
    assert 'id="script.kodi_mcp_test"' in addons_xml

    # Repo serving validation
    zip_url = payload["result"]["repo"]["zip_url"]
    zip_get = client.get(zip_url)
    assert zip_get.status_code == 200


def test_upload_then_publish_flow(tmp_path: Path, monkeypatch):
    """End-to-end local test for upload->artifact_id->publish flow."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPO_BASE_URL", "http://testserver")
    monkeypatch.setenv("KODI_JSONRPC_URL", "http://localhost:8080/jsonrpc")
    monkeypatch.setenv("KODI_BRIDGE_BASE_URL", "http://localhost:8765")

    import kodi_mcp_server.paths as paths

    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)

    import importlib
    import kodi_mcp_server.config as config
    import kodi_mcp_server.repo_server as repo_server

    importlib.reload(config)
    importlib.reload(repo_server)

    dev_repo = repo_root / "dev-repo"
    dev_repo.mkdir(parents=True, exist_ok=True)
    (dev_repo / "addons.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n</addons>\n',
        encoding="utf-8",
    )

    app = _make_app()
    client = TestClient(app)

    # Upload a tiny fake zip.
    upload = client.post(
        "/tools/artifacts/upload",
        files={"file": ("script.kodi_mcp_test-0.0.1.zip", b"PK\x03\x04", "application/zip")},
        data={"addon_id": "script.kodi_mcp_test", "version": "0.0.1"},
    )
    assert upload.status_code == 200
    upload_payload = upload.json()
    assert upload_payload.get("error") is None
    artifact = (upload_payload.get("result") or {}).get("artifact")
    artifact_id = (artifact or {}).get("artifact_id")
    assert isinstance(artifact_id, str) and artifact_id

    # Publish via artifact id.
    pub = client.post(
        "/tools/repo/publish_artifact",
        json={
            "artifact_id": artifact_id,
            "addon_id": "script.kodi_mcp_test",
            "addon_name": "Kodi MCP Test Script",
            "addon_version": "0.0.1",
            "provider_name": "kodi_mcp",
        },
    )
    assert pub.status_code == 200
    pub_payload = pub.json()
    assert pub_payload.get("error") is None

    # Repo serving validation
    addons_xml = (dev_repo / "addons.xml").read_text(encoding="utf-8")
    assert 'id="script.kodi_mcp_test"' in addons_xml
    zip_url = pub_payload["result"]["repo"]["zip_url"]
    zip_get = client.get(zip_url)
    assert zip_get.status_code == 200
