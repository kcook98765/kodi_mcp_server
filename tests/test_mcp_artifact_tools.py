import base64
import io
import zipfile
from pathlib import Path

import pytest


def _addon_zip_bytes(*, addon_id: str = "script.kodi_mcp_test", version: str = "0.0.1", name: str = "Kodi MCP Test Script") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            f"{addon_id}/addon.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<addon id="{addon_id}" name="{name}" version="{version}" provider-name="kodi_mcp">\n'
                '  <requires><import addon="xbmc.python" version="3.0.0"/></requires>\n'
                '  <extension point="xbmc.python.script" library="default.py"/>\n'
                "</addon>\n"
            ),
        )
        archive.writestr(f"{addon_id}/default.py", "print('ok')\n")
    return buf.getvalue()


def _zip_without_addon_xml() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("script.kodi_mcp_test/default.py", "print('ok')\n")
    return buf.getvalue()


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

    zip_bytes = _addon_zip_bytes()
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
    assert env["data"]["artifact"]["addon_id"] == "script.kodi_mcp_test"
    assert env["data"]["artifact"]["version"] == "0.0.1"
    assert env["data"]["artifact"]["addon_name"] == "Kodi MCP Test Script"

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


@pytest.mark.parametrize(
    ("zip_bytes", "args", "error_fragment"),
    [
        (b"not a zip", {}, "invalid zip file"),
        (_zip_without_addon_xml(), {}, "zip is missing script.kodi_mcp_test/addon.xml"),
        (_addon_zip_bytes(addon_id="script.other"), {"addon_id": "script.kodi_mcp_test"}, "addon.xml id mismatch"),
        (_addon_zip_bytes(version="0.0.2"), {"version": "0.0.1"}, "addon.xml version mismatch"),
    ],
)
@pytest.mark.asyncio
async def test_mcp_artifact_upload_validates_addon_zip(tmp_path: Path, monkeypatch, zip_bytes: bytes, args: dict, error_fragment: str):
    import importlib
    import json

    import kodi_mcp_server.paths as paths

    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)
    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", tmp_path / "repo", raising=False)

    import kodi_mcp_server.config as config

    importlib.reload(config)

    from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime
    from mcp.types import CallToolRequest, CallToolRequestParams

    runtime = build_runtime()
    server, _ = build_mcp_server(runtime)

    upload_args = {
        "zip_base64": base64.b64encode(zip_bytes).decode("ascii"),
        "filename": "upload.zip",
        **args,
    }
    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="artifact_upload_zip", arguments=upload_args),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert resp.root.isError is True
    assert env["ok"] is False
    assert error_fragment in env["error"]


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


@pytest.mark.asyncio
async def test_mcp_addon_execute_dispatches_jsonrpc():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        def __init__(self):
            self.calls = []

        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            self.calls.append({"addonid": addonid, "params": params, "wait": wait})
            return ResponseMessage(request_id="exec", result={"launched": True}, error=None)

    jsonrpc = _JsonRpc()
    server, _ = build_mcp_server({"bridge": object(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={"addonid": "plugin.kodi_world_poc", "wait": False, "params": {"mode": "test"}},
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert jsonrpc.calls == [{"addonid": "plugin.kodi_world_poc", "params": {"mode": "test"}, "wait": False}]


@pytest.mark.asyncio
async def test_repo_publish_stage_apply_artifact_reports_installed_version_mismatch(tmp_path: Path, monkeypatch):
    import importlib
    import json

    import kodi_mcp_server.paths as paths

    repo_root = tmp_path / "repo"
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)
    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)

    import kodi_mcp_server.config as config

    importlib.reload(config)

    from kodi_mcp_server.artifact_store import ArtifactStore
    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    store = ArtifactStore(root_dir=tmp_path / "artifacts")
    record = store.register_bytes(
        data=_addon_zip_bytes(version="0.0.2"),
        filename="script.kodi_mcp_test-0.0.2.zip",
        addon_id="script.kodi_mcp_test",
        version="0.0.2",
        addon_name="Kodi MCP Test Script",
    )

    async def _fake_stage(*, zip_path: str, repo_version=None, verify=True):
        return {"upload": {"transport_ok": True}, "state": {"dev_setup_available": True}}

    import kodi_mcp_server.milestone_a_bridge as milestone

    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage)

    class _Bridge:
        async def get_bridge_addon_info(self, addonid: str):
            return ResponseMessage(
                request_id="info",
                result={"installed": True, "enabled": True, "version": "0.0.1"},
                error=None,
            )

        async def execute_bridge_builtin(self, command: str, addonid: str | None = None):
            return ResponseMessage(request_id="builtin", result={"ok": True}, error=None)

    server, _ = build_mcp_server({"bridge": _Bridge(), "jsonrpc": object(), "notifications": None})
    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="repo_publish_stage_apply_artifact",
                arguments={
                    "artifact_id": record.artifact_id,
                    "addon_id": "script.kodi_mcp_test",
                    "addon_name": "Kodi MCP Test Script",
                    "addon_version": "0.0.2",
                    "timeout_seconds": 1,
                    "poll_interval_seconds": 1,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is False
    assert env["data"]["ok"] is False
    assert env["data"]["apply_verified"] is False
    assert env["data"]["apply_status"] == "installed_version_mismatch"
    assert env["data"]["can_retry"] is False
