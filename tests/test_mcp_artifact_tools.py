import base64
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_mcp_artifact_upload_and_publish(tmp_path: Path, monkeypatch):
    """Validate MCP tool surface for artifact upload + publish.

    This is intentionally local and does not require a running Kodi bridge.
    """

    # Patch paths for isolation *before* importing config-dependent modules.
    import kodi_mcp_server.paths as paths

    repo_root = tmp_path / "repo"
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)
    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)

    # Reload config after patching paths so REPO_ROOT follows our temp repo.
    import importlib
    import kodi_mcp_server.config as config

    importlib.reload(config)

    # Build MCP server runtime (no network calls made for these tools).
    from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime
    from mcp.types import CallToolRequest, CallToolRequestParams

    runtime = build_runtime()
    server, _ = build_mcp_server(runtime)

    # Upload a minimal zip header bytes (not a valid addon, but publish pipeline doesn't unzip).
    zip_bytes = b"PK\x03\x04"  # local file header signature
    zip_b64 = base64.b64encode(zip_bytes).decode("ascii")

    upload_req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="artifact_upload_zip",
            arguments={
                "zip_base64": zip_b64,
                "filename": "script.kodi_mcp_test-0.0.1.zip",
                "addon_id": "script.kodi_mcp_test",
                "version": "0.0.1",
            },
        ),
    )

    upload_result = await server.request_handlers[CallToolRequest](upload_req)
    payload = upload_result.root.model_dump()
    assert payload["isError"] is False
    text = payload["content"][0]["text"]

    import json

    env = json.loads(text)
    assert env["ok"] is True
    artifact_id = env["data"]["artifact"]["artifact_id"]
    assert isinstance(artifact_id, str) and artifact_id

    # Publish that artifact into dev repo.
    pub_req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="repo_publish_artifact",
            arguments={
                "artifact_id": artifact_id,
                "addon_id": "script.kodi_mcp_test",
                "addon_name": "Kodi MCP Test Script",
                "addon_version": "0.0.1",
                "provider_name": "kodi_mcp",
            },
        ),
    )
    pub_result = await server.request_handlers[CallToolRequest](pub_req)
    pub_payload = json.loads(pub_result.root.content[0].text)
    assert pub_payload["ok"] is True
    # Ensure we did not leak absolute server paths.
    result = pub_payload["data"]
    assert "zip_url" in result["repo"]
    assert "zip_path" not in json.dumps(result)

    # Confirm repo metadata exists.
    addons_xml = (repo_root / "dev-repo" / "addons.xml").read_text(encoding="utf-8")
    assert 'id="script.kodi_mcp_test"' in addons_xml


@pytest.mark.asyncio
async def test_mcp_repo_stage_current_dev_repo_builds_and_calls_bridge(tmp_path: Path, monkeypatch):
    """repo_stage_current_dev_repo builds zip from server repo state and stages via bridge helper.

    Bridge upload is monkeypatched so this test remains local.
    """

    import importlib
    import json

    import kodi_mcp_server.paths as paths

    repo_root = tmp_path / "repo"
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)
    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)

    # Ensure minimal dev-repo structure exists.
    dev_repo = repo_root / "dev-repo"
    dev_repo.mkdir(parents=True, exist_ok=True)
    (dev_repo / "addons.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n</addons>\n',
        encoding="utf-8",
    )
    (dev_repo / "addons.xml.md5").write_text("d41d8cd98f00b204e9800998ecf8427e  addons.xml\n", encoding="utf-8")

    import kodi_mcp_server.config as config
    importlib.reload(config)

    # Monkeypatch the actual bridge stage helper so no Kodi is required.
    import kodi_mcp_server.milestone_a_bridge as milestone

    called = {"zip_path": None}

    async def _fake_stage(*, zip_path: str, repo_version=None, verify=True):
        called["zip_path"] = zip_path
        return {"upload": {"transport_ok": True}, "state": {"dev_setup_available": True}}

    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage)

    from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime
    from mcp.types import CallToolRequest, CallToolRequestParams

    runtime = build_runtime()
    server, _ = build_mcp_server(runtime)

    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="repo_stage_current_dev_repo",
            arguments={"repo_version": "test", "verify": True},
        ),
    )
    resp = await server.request_handlers[CallToolRequest](req)
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    data = env["data"]
    assert data["ok"] is True
    assert called["zip_path"] is not None
    assert str(called["zip_path"]).endswith(".zip")
