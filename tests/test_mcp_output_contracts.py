"""MCP output-schema, structured-content, and annotation conformance."""

import base64
import json

import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from mcp.types import CallToolRequestParams, PaginatedRequestParams
from starlette.testclient import TestClient

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_mcp.output_contracts import apply_output_contract, output_schema_for
from kodi_mcp_server.models.messages import ResponseMessage
from tests.png_fixtures import png_rgba


_PNG = png_rgba([[(32, 48, 64, 255)]])


_SCHEMALESS_TOOLS = {"addon_execute", "jsonrpc_introspect"}

_READ_ONLY_TOOLS = {
    "kodi_status",
    "bridge_health",
    "bridge_status",
    "bridge_runtime_info",
    "bridge_bootstrap_status",
    "bridge_log_tail",
    "bridge_log_markers",
    "bridge_log_recent_errors",
    "kodi_gui_state",
    "addon_list",
    "addon_details",
    "addon_source_inspect",
    "addon_project_map_status",
    "addon_source_tree",
    "kodi_library_summary",
    "kodi_music_summary",
    "kodi_music_search",
    "kodi_music_browse",
    "kodi_settings_list",
    "kodi_setting_get",
    "kodi_artist_albums",
    "kodi_album_songs",
    "kodi_library_search",
    "kodi_library_browse",
    "kodi_tv_seasons",
    "kodi_tv_episodes",
    "kodi_player_active",
    "kodi_player_item",
    "jsonrpc_introspect",
    "kodi_notifications_sample",
    "managed_addon_list",
    "managed_addon_get",
    "managed_addon_validate_state",
}

_NONDESTRUCTIVE_MUTATIONS = {
    "bridge_write_log_marker",
    "kodi_gui_screenshot",
    "kodi_player_open",
    "kodi_player_seek",
    "kodi_player_pause",
    "kodi_player_stop",
    "artifact_upload_zip",
    "kodi_setting_set",
}

_DESTRUCTIVE_MUTATIONS = {
    "kodi_gui_action",
    "addon_execute",
    "managed_addon_register",
    "managed_addon_build_publish_and_stage",
    "managed_addon_build_publish_stage_and_apply",
    "repo_publish_artifact",
    "repo_stage_current_dev_repo",
    "repo_stage_and_apply_addon",
    "repo_publish_stage_apply_artifact",
    "addon_dev_loop",
}

_OPEN_WORLD_TOOLS = {"addon_execute"}


class _Bridge:
    async def get_bridge_health(self):
        return ResponseMessage(request_id="health", result={"status": "ok"}, error=None)

    async def gui_state(self):
        return ResponseMessage(
            request_id="gui-state",
            result={
                "ok": True,
                "current_window": "Home",
                "current_window_id": 10000,
                "current_dialog_id": 0,
                "current_control": "Movies",
                "conditions": {
                    "fullscreen_video": False,
                    "player_has_media": False,
                    "player_has_video": False,
                    "player_playing": False,
                    "player_paused": False,
                },
                "active_players": [],
            },
            error=None,
        )

    async def get_bridge_log_tail(self, lines: int):
        return ResponseMessage(
            request_id="log",
            result={"lines": ["INFO one", "ERROR two"][-lines:], "path": "/kodi.log"},
            error=None,
        )

    async def gui_screenshot(self, include_image: bool = False):
        result = {
            "ok": True,
            "path": "/screenshot.png",
            "filename": "screenshot.png",
            "size_bytes": len(_PNG),
            "content_type": "image/png",
        }
        if include_image:
            result["image_base64"] = base64.b64encode(_PNG).decode("ascii")
        return ResponseMessage(request_id="shot", result=result, error=None)


class _JsonRpc:
    async def get_jsonrpc_version(self):
        return ResponseMessage(
            request_id="version",
            result={"version": {"major": 13, "minor": 0, "patch": 0}},
            error=None,
        )

    async def get_application_properties(self):
        return ResponseMessage(
            request_id="application",
            result={
                "name": "Kodi",
                "version": {
                    "major": 20,
                    "minor": 5,
                    "revision": "20.5.0",
                    "tag": "stable",
                },
            },
            error=None,
        )


async def _list_tools(server):
    handler = server.get_request_handler("tools/list")
    return (await handler.handler(None, PaginatedRequestParams())).tools


async def _call(server, name, arguments):
    handler = server.get_request_handler("tools/call")
    return await handler.handler(
        None, CallToolRequestParams(name=name, arguments=arguments)
    )


def _text_envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_tools_list_advertises_reviewed_output_contract_scope_without_input_drift():
    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None}
    )
    tools = await _list_tools(server)
    by_name = {tool.name: tool for tool in tools}

    assert {name for name, tool in by_name.items() if tool.output_schema is None} == _SCHEMALESS_TOOLS
    for name, tool in by_name.items():
        if name in _SCHEMALESS_TOOLS:
            continue
        Draft202012Validator.check_schema(tool.output_schema)
        assert tool.output_schema["type"] == "object"

    assert by_name["kodi_status"].input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert by_name["addon_execute"].input_schema["anyOf"] == [
        {"required": ["addonid"]},
        {"required": ["addon_id"]},
    ]
    assert all(tool.input_schema["additionalProperties"] is False for tool in tools)


@pytest.mark.asyncio
async def test_success_and_failure_structured_content_validate_and_preserve_content(monkeypatch):
    import kodi_mcp_mcp.server_core as server_core

    monkeypatch.setattr(
        server_core,
        "store_screenshot_from_base64",
        lambda _: {
            "screenshot_id": "shot-1",
            "filename": "shot-1.png",
            "url": "http://server/screenshots/shot-1.png",
            "content_type": "image/png",
            "format": "png",
            "size_bytes": 4,
            "width": 1,
            "height": 1,
            "sha256": "fixture",
        },
    )
    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None}
    )
    tools = {tool.name: tool for tool in await _list_tools(server)}

    calls = [
        ("kodi_status", {}),
        ("kodi_gui_state", {}),
        ("bridge_log_tail", {"lines": 2, "max_bytes": 1024}),
        ("kodi_gui_screenshot", {"include_image": True, "store": True}),
        ("kodi_player_seek", {"playerid": 1}),
    ]
    results = {}
    for name, arguments in calls:
        result = await _call(server, name, arguments)
        results[name] = result
        assert result.structured_content is not None, name
        Draft202012Validator(tools[name].output_schema).validate(
            result.structured_content
        )

    assert results["kodi_player_seek"].is_error is True
    assert results["kodi_player_seek"].structured_content["error"]
    assert results["kodi_status"].structured_content == _text_envelope(
        results["kodi_status"]
    )
    assert results["kodi_status"].structured_content["data"]["kodi"] == {
        "status": "ok",
        "name": "Kodi",
        "version": {
            "major": 20,
            "minor": 5,
            "revision": "20.5.0",
            "tag": "stable",
        },
    }
    assert results["kodi_status"].structured_content["data"]["jsonrpc"]["version"] == {
        "major": 13,
        "minor": 0,
        "patch": 0,
    }
    assert results["kodi_gui_state"].structured_content == _text_envelope(
        results["kodi_gui_state"]
    )

    log_text = _text_envelope(results["bridge_log_tail"])
    assert log_text["data"]["lines"] == ["INFO one", "ERROR two"]
    assert "lines" not in results["bridge_log_tail"].structured_content["data"]
    assert len(json.dumps(results["bridge_log_tail"].structured_content)) < 2048

    screenshot = results["kodi_gui_screenshot"]
    assert [block.type for block in screenshot.content] == ["text", "image"]
    assert "image_base64" not in screenshot.content[0].text
    assert "image_base64" not in json.dumps(screenshot.structured_content)
    assert screenshot.structured_content["data"]["server_screenshot"]["url"].endswith(
        "shot-1.png"
    )


@pytest.mark.asyncio
async def test_runtime_output_drift_fails_closed_with_schema_valid_error():
    class _DriftedBridge(_Bridge):
        async def gui_state(self):
            return ResponseMessage(
                request_id="drifted", result={"unexpected": True}, error=None
            )

    server, _ = build_mcp_server(
        {"bridge": _DriftedBridge(), "jsonrpc": _JsonRpc(), "notifications": None}
    )
    tools = {tool.name: tool for tool in await _list_tools(server)}

    result = await _call(server, "kodi_gui_state", {})

    assert result.is_error is True
    assert result.structured_content["error_type"] == "output_contract_error"
    assert "output contract" in result.structured_content["error"]
    Draft202012Validator(tools["kodi_gui_state"].output_schema).validate(
        result.structured_content
    )


@pytest.mark.asyncio
async def test_screenshot_contract_failure_does_not_attach_image_content():
    class _DriftedScreenshotBridge(_Bridge):
        async def gui_screenshot(self, include_image: bool = False):
            result = {"ok": True, "size_bytes": len(_PNG)}
            if include_image:
                result["image_base64"] = base64.b64encode(_PNG).decode("ascii")
            return ResponseMessage(request_id="drifted-shot", result=result, error=None)

    server, _ = build_mcp_server(
        {
            "bridge": _DriftedScreenshotBridge(),
            "jsonrpc": _JsonRpc(),
            "notifications": None,
        }
    )

    result = await _call(
        server, "kodi_gui_screenshot", {"include_image": True, "store": False}
    )

    assert result.is_error is True
    assert result.structured_content["error_type"] == "output_contract_error"
    assert [block.type for block in result.content] == ["text"]


@pytest.mark.asyncio
async def test_downstream_failure_preserves_meaningful_error_without_inventing_type():
    class _FailedBridge(_Bridge):
        async def get_bridge_health(self):
            return ResponseMessage(request_id="failed", result=None, error="bridge down")

    server, _ = build_mcp_server(
        {"bridge": _FailedBridge(), "jsonrpc": _JsonRpc(), "notifications": None}
    )
    tools = {tool.name: tool for tool in await _list_tools(server)}

    result = await _call(server, "bridge_health", {})

    assert result.is_error is True
    assert result.structured_content["error"] == "bridge down"
    assert result.structured_content["error_type"] is None
    Draft202012Validator(tools["bridge_health"].output_schema).validate(
        result.structured_content
    )


@pytest.mark.asyncio
async def test_all_advertised_contracts_validate_canonical_success_and_failure_families():
    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None}
    )
    tools = {tool.name: tool for tool in await _list_tools(server)}
    empty_page = {
        "start": 0,
        "end": 0,
        "total": 0,
        "limit": 10,
        "has_more": False,
    }
    music_artist = {
        "id": 1,
        "media_type": "artist",
        "name": "Example Artist",
        "genres": [],
        "is_album_artist": True,
        "artwork": {},
    }
    music_album = {
        "id": 2,
        "media_type": "album",
        "title": "Example Album",
        "artists": ["Example Artist"],
        "artist_ids": [1],
        "year": 2026,
        "genres": [],
        "playcount": 0,
        "compilation": False,
        "duration_seconds": 120,
        "artwork": {},
    }
    setting_policy = {
        "id": "filelists.showextensions",
        "label": "Show file extensions",
        "help": "Show filename extensions in Kodi file lists.",
        "section": "media",
        "category": "filelists",
        "type": "boolean",
        "readable": True,
        "writable": True,
        "mutation_unavailable_reason": None,
        "policy": "explicit_allowlist",
        "supported_kodi_major": [19, 20, 21, 22],
        "constraints": {},
    }
    specific_data = {
        "kodi_status": {
            "server": {"status": "running"},
            "config": {"loaded": True},
            "jsonrpc": {
                "status": "ok",
                "version": {"major": 13, "minor": 0, "patch": 0},
            },
            "kodi": {
                "status": "ok",
                "name": "Kodi",
                "version": {
                    "major": 20,
                    "minor": 5,
                    "revision": "20.5.0",
                    "tag": "stable",
                },
            },
            "bridge": {"status": "ok"},
            "vision": {},
        },
        "kodi_gui_state": {
            "current_window": "Home",
            "current_window_id": 10000,
            "current_dialog_id": 0,
            "conditions": {},
            "active_players": [],
        },
        "kodi_gui_screenshot": {
            "content_type": "image/png",
            "size_bytes": 10,
            "server_screenshot": {"url": "http://server/shot.png", "size_bytes": 10},
        },
        "addon_source_inspect": {
            "ok": True,
            "addon_id": "plugin.example",
            "name": "Example",
            "version": "1.0.0",
            "extensions": [],
            "entrypoints": [],
            "tests": [],
            "project_map": {},
        },
        "addon_source_tree": {"ok": True, "entries": [], "truncated": False},
        "kodi_library_summary": {
            "counts": {"movies": 0, "tvshows": 0, "seasons": 0, "episodes": 0}
        },
        "kodi_music_summary": {
            "counts": {"artists": 0, "albums": 0, "songs": 0}
        },
        "kodi_music_search": {
            "query": "example",
            "media_type": "artist",
            "search": {"field": "artist", "operator": "contains"},
            "items": [],
            "empty": True,
            "pagination": empty_page,
        },
        "kodi_music_browse": {
            "category": "genres",
            "items": [],
            "empty": True,
            "pagination": empty_page,
        },
        "kodi_settings_list": {
            "items": [setting_policy],
            "empty": False,
            "pagination": {**empty_page, "end": 1, "total": 1},
        },
        "kodi_setting_get": {
            "setting": {
                **setting_policy,
                "value": True,
                "default": True,
                "enabled": True,
            }
        },
        "kodi_setting_set": {
            "setting_id": "filelists.showextensions",
            "before": True,
            "requested": False,
            "after": False,
            "changed": True,
            "verified": True,
        },
        "kodi_artist_albums": {
            "artist": music_artist,
            "items": [],
            "empty": True,
            "pagination": empty_page,
        },
        "kodi_album_songs": {
            "album": music_album,
            "items": [],
            "empty": True,
            "pagination": empty_page,
        },
        "kodi_library_search": {
            "query": "example",
            "media_type": "movie",
            "search": {"field": "title", "operator": "contains"},
            "items": [],
            "empty": True,
            "pagination": {"start": 0, "end": 0, "total": 0, "limit": 10, "has_more": False},
        },
        "kodi_library_browse": {
            "category": "recent_movies",
            "items": [],
            "empty": True,
            "pagination": {"start": 0, "end": 0, "total": 0, "limit": 10, "has_more": False},
        },
        "kodi_tv_seasons": {
            "tvshow": {"id": 1, "title": "Example"},
            "items": [],
            "empty": True,
            "pagination": {"start": 0, "end": 0, "total": 0, "limit": 10, "has_more": False},
        },
        "kodi_tv_episodes": {
            "tvshow": {"id": 1, "title": "Example"},
            "season": 1,
            "items": [],
            "empty": True,
            "pagination": {"start": 0, "end": 0, "total": 0, "limit": 10, "has_more": False},
        },
        "kodi_player_active": [],
        "managed_addon_validate_state": {
            "ok": True,
            "managed_addon_id": "plugin.example",
            "registry": {},
            "artifacts": {},
            "kodi_bridge": {},
            "summary": {
                "ready_for_build": False,
                "ready_for_publish": False,
                "ready_for_stage": False,
                "ready_for_kodi_install": False,
            },
        },
    }
    log_data = {
        "lines": ["large content stays only in TextContent"],
        "truncated": False,
        "truncation_direction": None,
        "max_bytes": 1024,
        "available_lines": 1,
        "available_bytes": 36,
        "returned_lines": 1,
        "returned_bytes": 36,
    }
    dynamic_data = {
        "kodi_player_open": "OK",
        "kodi_player_seek": {"percentage": 50.0},
        "kodi_player_pause": {"speed": 0},
        "kodi_player_stop": "OK",
    }

    for name, tool in tools.items():
        assert tool.output_schema == output_schema_for(name)
        if name in _SCHEMALESS_TOOLS:
            continue
        data = (
            log_data
            if name.startswith("bridge_log_")
            else specific_data.get(name, dynamic_data.get(name, {}))
        )
        success = {
            "ok": True,
            "tool": name,
            "data": data,
            "error": None,
            "error_type": None,
            "error_code": None,
            "latency_ms": 1,
            "request_id": "fixture",
            "raw": {},
        }
        rendered, structured = apply_output_contract(success)
        assert rendered["ok"] is True, name
        Draft202012Validator(tool.output_schema).validate(structured)

        failure = {
            **success,
            "ok": False,
            "data": None,
            "error": "representative failure",
            "error_type": "operation_failed",
        }
        _, structured_failure = apply_output_contract(failure)
        Draft202012Validator(tool.output_schema).validate(structured_failure)


@pytest.mark.asyncio
async def test_tool_annotations_are_conservative_and_semantically_reviewed():
    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None}
    )
    by_name = {tool.name: tool for tool in await _list_tools(server)}

    assert set(by_name) == (
        _READ_ONLY_TOOLS | _NONDESTRUCTIVE_MUTATIONS | _DESTRUCTIVE_MUTATIONS
    )
    for name, tool in by_name.items():
        annotations = tool.annotations
        assert annotations is not None, name
        assert annotations.read_only_hint is (name in _READ_ONLY_TOOLS), name
        assert annotations.open_world_hint is (name in _OPEN_WORLD_TOOLS), name
        if name in _READ_ONLY_TOOLS:
            assert annotations.destructive_hint is not True, name
            assert annotations.idempotent_hint is None, name
        else:
            assert annotations.destructive_hint is (name in _DESTRUCTIVE_MUTATIONS), name
            assert annotations.idempotent_hint is False, name


def _sse_payload(response):
    assert response.status_code == 200
    data_lines = [
        line[len("data:") :].strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert data_lines
    return json.loads(data_lines[-1])


def test_streamable_http_advertises_and_returns_valid_structured_contracts():
    from kodi_mcp_server.remote_mcp_app import create_remote_mcp

    remote_app, remote_lifespan = create_remote_mcp()

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
                    "clientInfo": {"name": "output-contracts", "version": "0"},
                },
            },
        )
        session_id = init.headers["mcp-session-id"]
        headers = {
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2025-11-25",
        }
        tools_body = _sse_payload(
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
        )
        tools = {tool["name"]: tool for tool in tools_body["result"]["tools"]}
        assert "outputSchema" in tools["kodi_status"]
        assert tools["kodi_status"]["annotations"]["readOnlyHint"] is True

        for request_id, name, arguments in [
            (3, "kodi_status", {}),
            (4, "kodi_player_seek", {"playerid": 1}),
            (5, "kodi_status", {}),
        ]:
            body = _sse_payload(
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
            structured = body["result"]["structuredContent"]
            Draft202012Validator(tools[name]["outputSchema"]).validate(structured)
            assert json.loads(body["result"]["content"][0]["text"])["tool"] == name

        assert body["result"]["isError"] is False
