from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolRequestParams
from starlette.testclient import TestClient

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ResponseMessage


PNG_SMALL = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _large_png_like(size: int = 1_500_000) -> bytes:
    """A deterministic PNG-signature/IHDR payload at realistic screenshot size."""
    prefix = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x05\x00\x00\x00\x02\xd0"
        b"\x08\x06\x00\x00\x00"
    )
    return prefix + (b"x" * (size - len(prefix)))


class _PayloadBridge:
    def __init__(self, *, log_lines: list[str] | None = None, screenshot: bytes = PNG_SMALL):
        self.log_lines = log_lines or ["INFO ordinary", "WARNING final"]
        self.screenshot = screenshot

    async def get_bridge_log_tail(self, lines: int):
        return ResponseMessage(
            request_id="payload-log",
            result={
                "lines": self.log_lines[-lines:],
                "path": "/fixture/kodi.log",
                "error": None,
                "addon_id": "service.kodi_mcp",
            },
            error=None,
        )

    async def get_bridge_log_markers(self, lines: int):
        markers = [line for line in self.log_lines[-lines:] if "[service.kodi_mcp]" in line]
        return ResponseMessage(
            request_id="payload-markers",
            result={"lines": markers, "path": "/fixture/kodi.log", "error": None},
            error=None,
        )

    async def gui_screenshot(self, include_image: bool = False):
        result = {
            "ok": True,
            "path": "/fixture/screenshot.png",
            "filename": "screenshot.png",
            "size_bytes": len(self.screenshot),
            "content_type": "image/png",
        }
        if include_image:
            result["image_base64"] = base64.b64encode(self.screenshot).decode("ascii")
        return ResponseMessage(request_id="payload-screenshot", result=result, error=None)

    async def get_bridge_health(self):
        return ResponseMessage(request_id="health", result={"status": "ok"}, error=None)


class _PayloadJsonRpc:
    async def get_jsonrpc_version(self):
        return ResponseMessage(request_id="version", result={"version": {"major": 13}}, error=None)


async def _call(server, name: str, arguments: dict):
    return await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=name, arguments=arguments)
    )


def _envelope(result):
    return json.loads(result.content[0].text)


def _fake_store(image_base64: str):
    raw = base64.b64decode(image_base64, validate=True)
    return {
        "screenshot_id": "stored-shot",
        "filename": "stored-shot.png",
        "url": "http://server/screenshots/stored-shot.png",
        "content_type": "image/png",
        "format": "png",
        "size_bytes": len(raw),
        "width": int.from_bytes(raw[16:20], "big"),
        "height": int.from_bytes(raw[20:24], "big"),
        "sha256": "fixture-sha256",
    }


@pytest.mark.asyncio
async def test_small_log_content_is_unchanged_and_reports_no_truncation():
    bridge = _PayloadBridge(log_lines=["INFO café", "WARNING final"])
    server, _ = build_mcp_server({"bridge": bridge, "jsonrpc": _PayloadJsonRpc(), "notifications": None})

    result = await _call(server, "bridge_log_tail", {"lines": 2, "max_bytes": 128})
    env = _envelope(result)

    assert result.is_error is False
    assert env["data"]["lines"] == ["INFO café", "WARNING final"]
    assert env["data"]["truncated"] is False
    assert env["data"]["truncation_direction"] is None
    assert env["data"]["returned_lines"] == 2
    assert env["data"]["returned_bytes"] == len("INFO café\nWARNING final".encode())
    assert "INFO café" not in json.dumps(env["raw"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_large_log_is_utf8_safe_tail_bounded_with_explicit_metadata():
    bridge = _PayloadBridge(log_lines=["old", "AéBéC", "tail"])
    server, _ = build_mcp_server({"bridge": bridge, "jsonrpc": _PayloadJsonRpc(), "notifications": None})

    result = await _call(server, "bridge_log_tail", {"lines": 3, "max_bytes": 11})
    env = _envelope(result)
    data = env["data"]
    rendered = "\n".join(data["lines"])

    assert data["lines"] == ["éBéC", "tail"]
    assert rendered.encode("utf-8").decode("utf-8") == rendered
    assert len(rendered.encode("utf-8")) == 11
    assert data["truncated"] is True
    assert data["truncation_direction"] == "start"
    assert data["available_lines"] == 3
    assert data["available_bytes"] == len("old\nAéBéC\ntail".encode())
    assert data["returned_lines"] == 2
    assert data["returned_bytes"] == 11
    assert data["max_bytes"] == 11


@pytest.mark.asyncio
async def test_log_max_bytes_above_transport_safe_maximum_is_rejected():
    bridge = _PayloadBridge()
    server, _ = build_mcp_server({"bridge": bridge, "jsonrpc": _PayloadJsonRpc(), "notifications": None})

    result = await _call(
        server, "bridge_log_tail", {"lines": 1, "max_bytes": 131_073}
    )
    env = _envelope(result)

    assert result.is_error is True
    assert env["error_type"] == "invalid_params"
    assert "max_bytes" in env["error"]


@pytest.mark.asyncio
async def test_recent_error_filter_is_applied_before_byte_bound():
    huge = "ERROR old " + ("x" * 200_000)
    bridge = _PayloadBridge(log_lines=[huge, "INFO ignored", "ERROR newest"])
    server, _ = build_mcp_server({"bridge": bridge, "jsonrpc": _PayloadJsonRpc(), "notifications": None})

    result = await _call(
        server,
        "bridge_log_recent_errors",
        {"lines": 3, "pattern": "ERROR", "max_bytes": 64},
    )
    data = _envelope(result)["data"]

    assert data["matching_lines"][-1] == "ERROR newest"
    assert data["count"] == 2
    assert data["returned_bytes"] <= 64
    assert data["truncated"] is True
    assert data["truncation_direction"] == "start"


@pytest.mark.asyncio
async def test_small_screenshot_uses_one_canonical_mcp_image_content(monkeypatch):
    import kodi_mcp_mcp.server_core as server_core

    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _fake_store)
    server, _ = build_mcp_server(
        {"bridge": _PayloadBridge(screenshot=PNG_SMALL), "jsonrpc": _PayloadJsonRpc(), "notifications": None}
    )

    result = await _call(
        server, "kodi_gui_screenshot", {"include_image": True, "store": True}
    )
    env = _envelope(result)

    assert result.is_error is False
    assert [item.type for item in result.content] == ["text", "image"]
    assert result.content[1].data == base64.b64encode(PNG_SMALL).decode("ascii")
    assert result.content[1].mime_type == "image/png"
    assert "image_base64" not in result.content[0].text
    assert env["data"]["inline_image"]["included"] is True
    assert env["data"]["inline_image"]["raw_size_bytes"] == len(PNG_SMALL)
    assert env["data"]["server_screenshot"]["sha256"] == "fixture-sha256"


@pytest.mark.asyncio
async def test_large_screenshot_degrades_to_artifact_with_explicit_omission(monkeypatch):
    import kodi_mcp_mcp.server_core as server_core

    large = _large_png_like()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _fake_store)
    server, _ = build_mcp_server(
        {"bridge": _PayloadBridge(screenshot=large), "jsonrpc": _PayloadJsonRpc(), "notifications": None}
    )

    result = await _call(
        server, "kodi_gui_screenshot", {"include_image": True, "store": True}
    )
    env = _envelope(result)

    assert result.is_error is False
    assert [item.type for item in result.content] == ["text"]
    assert len(result.content[0].text.encode()) < 10_000
    assert "image_base64" not in result.content[0].text
    assert env["data"]["inline_image"] == {
        "included": False,
        "raw_size_bytes": len(large),
        "max_raw_size_bytes": 524_288,
        "reason": "image exceeds inline MCP payload limit; use server_screenshot.url",
    }
    assert env["data"]["server_screenshot"]["size_bytes"] == len(large)


@pytest.mark.asyncio
async def test_large_inline_only_screenshot_errors_explicitly_and_next_request_works():
    large = _large_png_like()
    server, _ = build_mcp_server(
        {"bridge": _PayloadBridge(screenshot=large), "jsonrpc": _PayloadJsonRpc(), "notifications": None}
    )

    result = await _call(
        server, "kodi_gui_screenshot", {"include_image": True, "store": False}
    )
    env = _envelope(result)
    follow_up = await _call(server, "kodi_status", {})

    assert result.is_error is True
    assert env["ok"] is False
    assert env["error_type"] == "payload_too_large"
    assert env["raw"]["raw_size_bytes"] == len(large)
    assert env["raw"]["max_raw_size_bytes"] == 524_288
    assert "store=true" in env["error"]
    assert _envelope(follow_up)["ok"] is True


def _sse_payload(response):
    data_lines = [
        line[len("data:"):].strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert data_lines
    return json.loads(data_lines[-1])


def test_real_streamable_http_bounds_large_log_and_screenshot_and_stays_healthy(monkeypatch):
    import kodi_mcp_mcp.server_core as server_core

    huge_log = "ERROR metadata " + ("x" * 1_900_000)
    large_image = _large_png_like(1_500_000)
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _fake_store)
    server, _ = build_mcp_server(
        {
            "bridge": _PayloadBridge(log_lines=[huge_log], screenshot=large_image),
            "jsonrpc": _PayloadJsonRpc(),
            "notifications": None,
        }
    )
    manager = StreamableHTTPSessionManager(
        app=server, event_store=None, json_response=False, stateless=False
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", manager.handle_request)

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
                    "clientInfo": {"name": "payload-test", "version": "0"},
                },
            },
        )
        session_id = init.headers["mcp-session-id"]
        headers = {
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2025-11-25",
        }

        log_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "bridge_log_tail", "arguments": {"lines": 1}},
            },
            headers=headers,
        )
        log_body = _sse_payload(log_response)
        log_env = json.loads(log_body["result"]["content"][0]["text"])
        assert log_response.headers["content-type"].startswith("text/event-stream")
        assert len(log_response.content) < 200_000
        assert log_env["data"]["truncated"] is True
        assert log_env["data"]["returned_bytes"] == 131_072
        assert log_body["result"]["content"][0]["text"].count("ERROR metadata") <= 1

        screenshot_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "kodi_gui_screenshot",
                    "arguments": {"include_image": True, "store": True},
                },
            },
            headers=headers,
        )
        screenshot_body = _sse_payload(screenshot_response)
        screenshot_env = json.loads(screenshot_body["result"]["content"][0]["text"])
        assert len(screenshot_response.content) < 20_000
        assert [item["type"] for item in screenshot_body["result"]["content"]] == ["text"]
        assert screenshot_env["data"]["inline_image"]["included"] is False
        assert "image_base64" not in screenshot_body["result"]["content"][0]["text"]

        status_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "kodi_status", "arguments": {}},
            },
            headers=headers,
        )
        status_body = _sse_payload(status_response)
        assert json.loads(status_body["result"]["content"][0]["text"])["ok"] is True
