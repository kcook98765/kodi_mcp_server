from __future__ import annotations

import asyncio
import json

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.tools.bridge import BridgeTool
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient


def _run(coroutine):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def test_http_client_staged_status_uses_held_client_get_and_returns_identity(monkeypatch):
    client = HttpBridgeClient("http://bridge.invalid:8765", token="held-token")
    expected = ResponseMessage(
        request_id="status-response",
        result={
            "ok": True,
            "exists": False,
            "size_bytes": None,
            "sha256": None,
            "metadata_present": True,
            "metadata_consistent": False,
        },
        error=None,
    )
    captured = {}

    async def retry(method, *args, request_id, max_retries=1, **kwargs):
        captured.update(
            method=method,
            args=args,
            request_id=request_id,
            max_retries=max_retries,
            kwargs=kwargs,
        )
        return expected

    monkeypatch.setattr(client, "_retry_wrapper", retry)

    actual = _run(client.staged_repo_status())

    assert actual is expected
    assert captured == {
        "method": client._make_request,
        "args": ("GET", "/repo/staged/status"),
        "request_id": "bridge-staged-repository-status",
        "max_retries": 1,
        "kwargs": {},
    }
    assert client.base_url == "http://bridge.invalid:8765"
    assert client._auth_headers() == {"X-Kodi-MCP-Token": "held-token"}


def test_http_client_staged_status_preserves_failure_without_adding_paths(monkeypatch):
    client = HttpBridgeClient("http://bridge.invalid:8765", token="held-token")
    calls = []
    bridge_payload = {
        "transport": {"ok": True},
        "result": {
            "ok": False,
            "error_code": "STAGED_REPOSITORY_READ_FAILED",
            "message": "staged repository artifact could not be read",
        },
    }

    def request(method, path):
        calls.append((method, path))
        return ResponseMessage(
            request_id="bridge-request",
            result=bridge_payload,
            error="http error 500: Internal Server Error",
            error_type=ErrorType.SERVER_ERROR,
            error_code=500,
            latency_ms=7,
        )

    monkeypatch.setattr(client, "_make_request", request)

    result = _run(client.staged_repo_status())

    assert calls == [("GET", "/repo/staged/status")]
    assert result.result is bridge_payload
    assert result.error == "http error 500: Internal Server Error"
    assert result.error_type == ErrorType.SERVER_ERROR
    assert result.error_code == 500
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    for forbidden in ("special://", "/home/user/.kodi", "bridge.invalid:8765", "held-token"):
        assert forbidden not in serialized


def test_bridge_tool_staged_status_delegates_once_and_returns_identity():
    expected = ResponseMessage(
        request_id="status-response",
        result={
            "ok": True,
            "exists": True,
            "size_bytes": 3,
            "sha256": "a" * 64,
            "metadata_present": True,
            "metadata_consistent": True,
        },
        error=None,
    )

    class Client:
        def __init__(self):
            self.calls = 0

        async def staged_repo_status(self):
            self.calls += 1
            return expected

    client = Client()
    tool = BridgeTool(client)  # type: ignore[arg-type]

    actual = _run(tool.get_staged_repo_status())

    assert actual is expected
    assert client.calls == 1
