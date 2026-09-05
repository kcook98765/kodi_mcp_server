"""OpenAPI contract regression tests for the composed HTTP app."""

import warnings

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from kodi_mcp_server.main import app


def _openapi_operations():
    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()
    operations = {
        (method.upper(), path): operation
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
    }
    return operations, caught


def test_openapi_operation_ids_are_unique_without_duplicate_warning():
    operations, caught = _openapi_operations()
    operation_ids = [operation["operationId"] for operation in operations.values()]

    assert len(operation_ids) == len(set(operation_ids))
    assert not [
        warning
        for warning in caught
        if "Duplicate Operation ID" in str(warning.message)
    ]


def test_openapi_preserves_expected_routes_methods_and_schema_visibility():
    operations, _ = _openapi_operations()

    expected = {
        ("GET", "/health"),
        ("GET", "/status"),
        ("GET", "/repo-health"),
        ("GET", "/repo/health"),
        ("GET", "/bootstrap/service.kodi_mcp.zip"),
        ("HEAD", "/bootstrap/service.kodi_mcp.zip"),
        ("GET", "/repo/install/latest.zip"),
        ("GET", "/repo/install/repository.kodi-mcp-latest.zip"),
        ("GET", "/tools/get_active_players"),
        ("POST", "/tools/write_bridge_log_marker"),
    }
    assert expected <= operations.keys()
    assert operations[("GET", "/bootstrap/service.kodi_mcp.zip")][
        "operationId"
    ] == "get_bootstrap_bridge_zip"
    assert operations[("HEAD", "/bootstrap/service.kodi_mcp.zip")][
        "operationId"
    ] == "head_bootstrap_bridge_zip"
    assert not any(path == "/mcp" or path.startswith("/mcp/") for _, path in operations)
    assert not any(path.startswith("/repo/content/") for _, path in operations)


def test_health_behavior_is_unchanged():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "kodi_mcp_server",
    }


def test_repository_install_compatibility_alias_remains_routable(tmp_path, monkeypatch):
    from fastapi import FastAPI

    import kodi_mcp_server.repo_server as repo_server

    repo_root = tmp_path / "repo"
    repo_addon = tmp_path / "repo-addon"
    repo_addon.mkdir()
    payload = b"PK\x03\x04test-only-repository-package"
    from kodi_mcp_server.repository_addon_manifest import load_repository_addon_manifest

    (repo_addon / load_repository_addon_manifest().artifact_filename).write_bytes(payload)
    monkeypatch.setattr(repo_server, "REPO_ROOT", repo_root)
    isolated_app = FastAPI()
    isolated_app.include_router(repo_server.router)

    response = TestClient(isolated_app).get(
        "/repo/install/repository.kodi-mcp-latest.zip"
    )

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/zip"


def test_bootstrap_methods_and_mcp_mount_remain_routable():
    api_routes = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert ("GET", "/bootstrap/service.kodi_mcp.zip") in api_routes
    assert ("HEAD", "/bootstrap/service.kodi_mcp.zip") in api_routes
    assert any(isinstance(route, Mount) and route.path == "/mcp" for route in app.routes)
