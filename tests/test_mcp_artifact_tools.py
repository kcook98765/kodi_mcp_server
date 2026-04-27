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


def _write_source_addon(root: Path, *, addon_id: str = "script.kodi_mcp_test", version: str = "0.0.1") -> Path:
    addon_dir = root / addon_id
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.xml").write_text(
        (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<addon id="{addon_id}" name="Kodi MCP Test Script" version="{version}" provider-name="kodi_mcp">\n'
            '  <requires><import addon="xbmc.python" version="3.0.0"/></requires>\n'
            '  <extension point="xbmc.python.script" library="default.py"/>\n'
            "</addon>\n"
        ),
        encoding="utf-8",
    )
    (addon_dir / "default.py").write_text("print('ok')\n", encoding="utf-8")
    (addon_dir / "PROJECT_MAP.md").write_text("# Project Map\n", encoding="utf-8")
    return addon_dir


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


@pytest.mark.asyncio
async def test_mcp_addon_source_tools_inspect_project_map_and_tree(tmp_path: Path, monkeypatch):
    import json

    source_dir = _write_source_addon(tmp_path / "source")
    monkeypatch.setenv("KODI_MCP_SOURCE_ROOTS", str(tmp_path))

    from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime
    from mcp.types import CallToolRequest, CallToolRequestParams

    server, _ = build_mcp_server(build_runtime())

    async def call(name: str, arguments: dict):
        resp = await server.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name=name, arguments=arguments),
            )
        )
        return json.loads(resp.root.content[0].text)

    inspect_env = await call("addon_source_inspect", {"source_path": str(source_dir)})
    assert inspect_env["ok"] is True
    assert inspect_env["data"]["addon_id"] == "script.kodi_mcp_test"
    assert inspect_env["data"]["version"] == "0.0.1"
    assert inspect_env["data"]["project_map"]["exists"] is True
    assert "default.py" in inspect_env["data"]["entrypoints"]

    map_env = await call("addon_project_map_status", {"source_path": str(source_dir)})
    assert map_env["ok"] is True
    assert map_env["data"]["project_map_exists"] is True

    tree_env = await call("addon_source_tree", {"source_path": str(source_dir), "max_entries": 10})
    assert tree_env["ok"] is True
    assert {"path": "default.py", "type": "file", "size_bytes": 12} in tree_env["data"]["entries"]


@pytest.mark.asyncio
async def test_mcp_addon_source_tools_translate_agent_workspace_paths(tmp_path: Path, monkeypatch):
    import json

    source_dir = _write_source_addon(tmp_path / "agent_mcp_probe_script" / "addon")
    monkeypatch.setenv("KODI_MCP_SOURCE_ROOTS", str(tmp_path))

    import kodi_mcp_mcp.server_core as server_core
    from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime
    from mcp.types import CallToolRequest, CallToolRequestParams

    def _test_translate(source_path: str) -> str:
        return source_path.replace("/srv/workspaces/", f"{tmp_path}/", 1)

    monkeypatch.setattr(server_core, "_translate_agent_source_path", _test_translate)
    server, _ = build_mcp_server(build_runtime())
    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_source_inspect",
                arguments={"source_path": "/srv/workspaces/agent_mcp_probe_script/addon/script.kodi_mcp_test"},
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["addon_id"] == "script.kodi_mcp_test"
    assert env["data"]["addon_dir"] == str(source_dir)


@pytest.mark.asyncio
async def test_mcp_bridge_log_recent_errors_filters_log_lines():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _Bridge:
        async def get_bridge_log_tail(self, lines: int):
            return ResponseMessage(
                request_id="log",
                result={"lines": ["INFO normal", "WARNING addon slow", "ERROR plugin failed", "Traceback details"]},
                error=None,
            )

    server, _ = build_mcp_server({"bridge": _Bridge(), "jsonrpc": object(), "notifications": None})
    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="bridge_log_recent_errors",
                arguments={"lines": 50, "pattern": "addon|plugin"},
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["count"] == 2
    assert env["data"]["matching_lines"] == ["WARNING addon slow", "ERROR plugin failed"]


@pytest.mark.asyncio
async def test_mcp_addon_dev_loop_alias_dispatches_one_shot(monkeypatch):
    import json

    import kodi_mcp_mcp.server_core as server_core
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    called = {}

    async def _fake_dev_loop(**kwargs):
        called.update(kwargs)
        return {"ok": True, "apply_verified": True, "installed_version_after": kwargs["addon_version"]}

    monkeypatch.setattr(server_core, "_repo_publish_stage_apply_artifact", _fake_dev_loop)

    server, _ = build_mcp_server({"bridge": object(), "jsonrpc": object(), "notifications": None})
    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_dev_loop",
                arguments={
                    "artifact_id": "artifact-1",
                    "addon_id": "script.kodi_mcp_test",
                    "addon_name": "Kodi MCP Test Script",
                    "addon_version": "0.0.5",
                    "timeout_seconds": 7,
                    "poll_interval_seconds": 1,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["apply_verified"] is True
    assert called["artifact_id"] == "artifact-1"
    assert called["runtime_bridge_tool"] is not None
    assert called["runtime_jsonrpc_tool"] is not None


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

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    jsonrpc = _JsonRpc()
    server, _ = build_mcp_server({"bridge": object(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={
                    "addonid": "plugin.kodi_world_poc",
                    "wait": False,
                    "params": {"mode": "test"},
                    "observe_player_seconds": 0,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["dispatch_ok"] is True
    assert env["data"]["verified"] is None
    assert env["data"]["player_observation"]["player_started"] is False
    assert "dispatch succeeded" in env["data"]["note"]
    assert jsonrpc.calls == [{"addonid": "plugin.kodi_world_poc", "params": {"mode": "test"}, "wait": False}]


@pytest.mark.asyncio
async def test_mcp_addon_execute_accepts_addon_id_alias():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        def __init__(self):
            self.calls = []

        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            self.calls.append({"addonid": addonid, "params": params, "wait": wait})
            return ResponseMessage(request_id="exec", result="OK", error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    jsonrpc = _JsonRpc()
    server, _ = build_mcp_server({"bridge": object(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={"addon_id": "plugin.kodi_world_poc", "observe_player_seconds": 0},
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["addonid"] == "plugin.kodi_world_poc"
    assert jsonrpc.calls == [{"addonid": "plugin.kodi_world_poc", "params": {}, "wait": False}]


@pytest.mark.asyncio
async def test_mcp_addon_execute_includes_gui_state_by_default():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            return ResponseMessage(request_id="exec", result="OK", error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    class _Bridge:
        async def gui_state(self):
            return ResponseMessage(
                request_id="gui",
                result={
                    "current_window": "Videos",
                    "current_control": "[..]",
                    "conditions": {"fullscreen_video": False},
                },
                error=None,
            )

    server, _ = build_mcp_server({"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={"addonid": "plugin.kodi_world_poc", "observe_player_seconds": 0},
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["gui_state"]["captured"] is True
    assert env["data"]["gui_state"]["source"] == "kodi_gui_state"
    assert env["data"]["gui_state"]["state"]["current_window"] == "Videos"
    assert env["data"]["gui_state"]["request_id"] == "gui"


@pytest.mark.asyncio
async def test_mcp_addon_execute_can_disable_default_gui_state():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            return ResponseMessage(request_id="exec", result="OK", error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    class _Bridge:
        async def gui_state(self):
            raise AssertionError("gui_state should not be called when include_gui_state is false")

    server, _ = build_mcp_server({"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={
                    "addonid": "plugin.kodi_world_poc",
                    "include_gui_state": False,
                    "observe_player_seconds": 0,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["gui_state"] == {
        "captured": False,
        "source": "disabled",
        "state": None,
        "error": None,
        "request_id": None,
    }


@pytest.mark.asyncio
async def test_mcp_addon_execute_can_verify_player_started():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            return ResponseMessage(request_id="exec", result={"launched": True}, error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[{"playerid": 1, "type": "video"}], error=None)

    server, _ = build_mcp_server({"bridge": object(), "jsonrpc": _JsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={
                    "addonid": "plugin.kodi_world_poc",
                    "wait": False,
                    "params": {"mode": "run"},
                    "expect_player": True,
                    "player_timeout_seconds": 1,
                    "poll_interval_ms": 100,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["player_verification"]["player_started"] is True
    assert env["data"]["player_verification"]["active_players"] == [{"playerid": 1, "type": "video"}]


@pytest.mark.asyncio
async def test_mcp_addon_execute_verification_fails_when_player_missing():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            return ResponseMessage(request_id="exec", result={"launched": True}, error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    server, _ = build_mcp_server({"bridge": object(), "jsonrpc": _JsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={
                    "addonid": "plugin.kodi_world_poc",
                    "expect_player": True,
                    "player_timeout_seconds": 1,
                    "poll_interval_ms": 100,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is False
    assert env["error_type"] == "verification_failed"
    assert env["data"]["player_verification"]["player_started"] is False
    assert "playback-only assertion" in env["data"]["verification_guidance"][0]


@pytest.mark.asyncio
async def test_mcp_addon_execute_can_verify_window_state():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            return ResponseMessage(request_id="exec", result="OK", error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    class _Bridge:
        async def gui_state(self):
            return ResponseMessage(
                request_id="gui",
                result={
                    "current_window": "Kodi World PoC Navigation",
                    "conditions": {"fullscreen_video": False},
                },
                error=None,
            )

    server, _ = build_mcp_server({"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={
                    "addonid": "plugin.kodi_world_poc",
                    "expect_window": "world poc",
                    "expect_fullscreen": False,
                    "window_timeout_seconds": 1,
                    "window_poll_interval_ms": 100,
                    "observe_player_seconds": 0,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is True
    assert env["data"]["verified"] is True
    assert env["data"]["gui_verification"]["matched"] is True
    assert env["data"]["gui_verification"]["window_matched"] is True
    assert env["data"]["gui_verification"]["fullscreen_matched"] is True
    assert env["data"]["gui_state"]["source"] == "gui_verification"
    assert env["data"]["gui_state"]["state"]["current_window"] == "Kodi World PoC Navigation"


@pytest.mark.asyncio
async def test_mcp_addon_execute_window_verification_fails_when_window_missing():
    import json

    from kodi_mcp_server.models.messages import ResponseMessage
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    class _JsonRpc:
        async def execute_addon(self, addonid: str, params=None, wait: bool = False):
            return ResponseMessage(request_id="exec", result="OK", error=None)

        async def get_active_players(self):
            return ResponseMessage(request_id="active", result=[], error=None)

    class _Bridge:
        async def gui_state(self):
            return ResponseMessage(
                request_id="gui",
                result={
                    "current_window": "Videos",
                    "conditions": {"fullscreen_video": False},
                },
                error=None,
            )

    server, _ = build_mcp_server({"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="addon_execute",
                arguments={
                    "addonid": "plugin.kodi_world_poc",
                    "expect_window": "navigation",
                    "window_timeout_seconds": 1,
                    "window_poll_interval_ms": 100,
                    "observe_player_seconds": 0,
                },
            ),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert env["ok"] is False
    assert env["error_type"] == "verification_failed"
    assert env["data"]["dispatch_ok"] is True
    assert env["data"]["verified"] is False
    assert env["data"]["gui_verification"]["matched"] is False
    assert env["data"]["gui_verification"]["last_gui_state"]["current_window"] == "Videos"


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
