from fastapi import FastAPI
from fastapi.testclient import TestClient

from kodi_mcp_server.orchestration_locking import OrchestrationLockTimeout


def _client() -> TestClient:
    from kodi_mcp_server.mcp_app import configure_mcp_app

    app = FastAPI()
    configure_mcp_app(app)
    return TestClient(app)


def _assert_busy(response):
    assert response.status_code == 423
    payload = response.json()
    assert payload["error"] == "timed out waiting for global workflow lock"
    assert payload["error_type"] == "resource_busy"
    assert payload["error_code"] == "ORCHESTRATION_LOCK_TIMEOUT"
    assert payload["result"] is None
    rendered = response.text.lower()
    for forbidden in ("/workspaces", "orchestration-locks", "http://", "token"):
        assert forbidden not in rendered


def test_legacy_http_artifact_upload_maps_lock_timeout_to_shared_busy_response(
    monkeypatch
):
    import kodi_mcp_server.artifact_store as artifact_store

    def locked(*args, **kwargs):
        raise OrchestrationLockTimeout("global_workflow")

    monkeypatch.setattr(artifact_store.ArtifactStore, "register_filelike", locked)
    response = _client().post(
        "/tools/artifacts/upload",
        files={"file": ("addon.zip", b"zip", "application/zip")},
    )
    _assert_busy(response)


def test_repository_mutation_http_route_uses_same_lock_timeout_mapping(monkeypatch):
    import kodi_mcp_server.mcp_app as mcp_app
    from kodi_mcp_server.tools.repo import RepoTool

    tool = RepoTool()

    def locked(**kwargs):
        raise OrchestrationLockTimeout("global_workflow")

    monkeypatch.setattr(tool.publisher, "publish_addon", locked)
    monkeypatch.setattr(mcp_app, "build_repo_tool", lambda: tool)
    response = _client().post(
        "/tools/publish_addon_to_repo",
        json={
            "addon_zip_path": "/safe/test.zip",
            "addon_id": "plugin.test",
            "addon_name": "Test",
            "addon_version": "1.0.0",
            "provider_name": "test",
        },
    )
    _assert_busy(response)


def test_artifact_publish_http_route_uses_same_lock_timeout_mapping(monkeypatch):
    import kodi_mcp_server.dev_loop_artifacts as artifacts

    def locked(**kwargs):
        raise OrchestrationLockTimeout("global_workflow")

    monkeypatch.setattr(artifacts, "repo_publish_artifact", locked)
    response = _client().post(
        "/tools/repo/publish_artifact",
        json={
            "artifact_id": "artifact",
            "addon_id": "plugin.test",
            "addon_name": "Test",
            "addon_version": "1.0.0",
            "provider_name": "test",
        },
    )
    _assert_busy(response)
