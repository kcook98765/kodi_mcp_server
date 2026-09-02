"""Issue #9 settings administration through real StreamableHTTP."""

import json

from fastapi import FastAPI
from jsonschema import Draft202012Validator
from starlette.testclient import TestClient

from kodi_mcp_server.models.messages import ResponseMessage


class _Bridge:
    pass


class _JsonRpc:
    def __init__(self):
        self.show_extensions = True
        self.calls = []

    async def execute_jsonrpc(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "Settings.GetSettings":
            return ResponseMessage(
                request_id="settings",
                result={
                    "settings": [
                        {
                            "id": "filelists.showextensions",
                            "label": "Show file extensions",
                            "help": "Show filename extensions in Kodi file lists.",
                            "type": "boolean",
                            "level": "standard",
                            "enabled": True,
                            "parent": "",
                            "default": True,
                            "value": self.show_extensions,
                        },
                        {
                            "id": "filelists.showhidden",
                            "label": "Show hidden files and directories",
                            "help": "Potentially exposes hidden entries.",
                            "type": "boolean",
                            "level": "advanced",
                            "enabled": True,
                            "parent": "",
                            "default": False,
                            "value": False,
                        },
                    ]
                },
                error=None,
            )
        if method == "Settings.SetSettingValue":
            assert params["setting"] == "filelists.showextensions"
            assert isinstance(params["value"], bool)
            self.show_extensions = params["value"]
            return ResponseMessage(request_id="set", result=True, error=None)
        raise AssertionError(f"unexpected method: {method}")


def _sse(response):
    assert response.status_code == 200
    payloads = [
        line.removeprefix("data:").strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert payloads
    return json.loads(payloads[-1])


def test_settings_remote_workflow_over_streamable_http(monkeypatch):
    import kodi_mcp_server.remote_mcp_app as remote_mcp_app

    jsonrpc = _JsonRpc()
    monkeypatch.setattr(
        remote_mcp_app,
        "build_runtime",
        lambda: {"bridge": _Bridge(), "jsonrpc": jsonrpc, "notifications": None},
    )
    remote_app, remote_lifespan = remote_mcp_app.create_remote_mcp()

    async def lifespan(_: FastAPI):
        async with remote_lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", remote_app)

    with TestClient(app) as client:
        init = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "settings-acceptance", "version": "0"},
                },
            },
        )
        headers = {
            "mcp-session-id": init.headers["mcp-session-id"],
            "mcp-protocol-version": "2025-11-25",
        }
        tools_body = _sse(
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
        )
        tools = {tool["name"]: tool for tool in tools_body["result"]["tools"]}
        setting_names = {"kodi_settings_list", "kodi_setting_get", "kodi_setting_set"}
        assert setting_names <= tools.keys()
        for name in setting_names:
            Draft202012Validator.check_schema(tools[name]["inputSchema"])
            Draft202012Validator.check_schema(tools[name]["outputSchema"])
        assert tools["kodi_settings_list"]["annotations"]["readOnlyHint"] is True
        assert tools["kodi_setting_get"]["annotations"]["readOnlyHint"] is True
        assert tools["kodi_setting_set"]["annotations"] == {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        }

        calls = [
            ("kodi_settings_list", {"writable": True, "limit": 5}),
            ("kodi_setting_get", {"setting_id": "filelists.showextensions"}),
            (
                "kodi_setting_set",
                {"setting_id": "filelists.showextensions", "value": "false"},
            ),
            (
                "kodi_setting_set",
                {"setting_id": "services.webserver", "value": True},
            ),
            (
                "kodi_setting_set",
                {"setting_id": "filelists.showextensions", "value": False},
            ),
            ("kodi_setting_get", {"setting_id": "filelists.showextensions"}),
            (
                "kodi_setting_set",
                {"setting_id": "filelists.showextensions", "value": True},
            ),
            ("kodi_setting_get", {"setting_id": "filelists.showextensions"}),
        ]
        results = []
        for request_id, (name, arguments) in enumerate(calls, start=3):
            body = _sse(
                client.post(
                    "/mcp/",
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                    headers=headers,
                )
            )
            result = body["result"]
            Draft202012Validator(tools[name]["outputSchema"]).validate(
                result["structuredContent"]
            )
            results.append(result)

        assert results[0]["structuredContent"]["data"]["items"]
        assert results[1]["structuredContent"]["data"]["setting"]["value"] is True
        assert results[2]["isError"] is True
        assert results[2]["structuredContent"]["error_type"] == "invalid_operation"
        assert results[3]["isError"] is True
        assert results[4]["structuredContent"]["data"] == {
            "setting_id": "filelists.showextensions",
            "before": True,
            "requested": False,
            "after": False,
            "changed": True,
            "verified": True,
        }
        assert results[5]["structuredContent"]["data"]["setting"]["value"] is False
        assert results[6]["structuredContent"]["data"]["after"] is True
        assert results[7]["structuredContent"]["data"]["setting"]["value"] is True
        assert [method for method, _ in jsonrpc.calls].count("Settings.SetSettingValue") == 2
