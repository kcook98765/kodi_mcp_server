"""Regression tests for the JSON-RPC transport retry contract.

``_retry_wrapper`` promises one automatic retry for ``SAFE_READ_METHODS`` on
transient network failures (``socket.timeout`` / ``URLError``).
``_send_once`` converts those failures into typed error ``ResponseMessage``s
(TIMEOUT / NETWORK_ERROR) instead of re-raising, so the retry must be driven
off the returned response's ``error_type``, not escaped exceptions.

All tests are deterministic: the outer HTTP primitive
(``urllib_request.urlopen``) is faked to return or raise controlled failures.
No live network.
"""
import asyncio
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kodi_mcp_server.models.messages import ErrorType, RequestMessage
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport


class _FakeResponse:
    """Minimal stand-in for ``urlopen``'s context-manager response."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_fake_urlopen(calls, outcomes):
    """Build a fake ``urlopen`` that pops outcomes in call order.

    Each outcome is either an exception instance (raised) or a str
    (JSON-RPC success body, wrapped in ``_FakeResponse``).
    """

    def fake_urlopen(http_request, timeout=None):
        idx = len(calls)
        if idx >= len(outcomes):
            raise AssertionError(f"unexpected urlopen call #{idx + 1}")
        outcome = outcomes[idx]
        calls.append(idx)
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(outcome.encode("utf-8"))

    return fake_urlopen


def _success_body(request_id: str = "retry-test") -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
    )


def _transport() -> HttpJsonRpcTransport:
    return HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )


def _run(transport: HttpJsonRpcTransport, method: str):
    """Run one request on an explicit, closed event loop (no install)."""
    request = RequestMessage(
        request_id="retry-test",
        command="execute_jsonrpc",
        args={"method": method, "params": {}},
    )
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(transport.send_request(request))
    finally:
        loop.close()


def _run_with_fakes(transport: HttpJsonRpcTransport, method: str, outcomes):
    calls = []
    with patch(
        "kodi_mcp_server.transport.http_jsonrpc.urllib_request.urlopen",
        _make_fake_urlopen(calls, outcomes),
    ):
        response = _run(transport, method)
    return response, calls


def test_safe_read_method_retries_after_timeout_then_succeeds():
    """System.GetProperties: socket.timeout then success -> 2 attempts, success."""
    response, calls = _run_with_fakes(
        _transport(),
        "System.GetProperties",
        [socket.timeout("timed out"), _success_body()],
    )
    assert response.error is None
    assert response.result == {"ok": True}
    assert len(calls) == 2


def test_safe_read_method_retries_after_connection_error_then_succeeds():
    """Player.GetActivePlayers: URLError then success -> 2 attempts, success."""
    response, calls = _run_with_fakes(
        _transport(),
        "Player.GetActivePlayers",
        [URLError(OSError("Connection refused")), _success_body()],
    )
    assert response.error is None
    assert response.result == {"ok": True}
    assert len(calls) == 2


def test_safe_read_method_retries_after_timeout_wrapped_in_urlerror_then_succeeds():
    """Addons.GetAddons: URLError(socket.timeout) then success -> 2 attempts."""
    response, calls = _run_with_fakes(
        _transport(),
        "Addons.GetAddons",
        [URLError(socket.timeout("timed out")), _success_body()],
    )
    assert response.error is None
    assert response.result == {"ok": True}
    assert len(calls) == 2


def test_safe_read_method_exhausts_retry_on_persistent_timeout():
    """System.GetProperties: socket.timeout on both attempts -> exactly 2 calls,
    typed TIMEOUT error."""
    response, calls = _run_with_fakes(
        _transport(),
        "System.GetProperties",
        [socket.timeout("timed out"), socket.timeout("timed out")],
    )
    assert len(calls) == 2
    assert response.error == "request timeout"
    assert response.error_type == ErrorType.TIMEOUT


def test_safe_read_method_exhausts_retry_on_persistent_connection_error():
    """Files.GetDirectory: URLError on both attempts -> exactly 2 calls,
    typed NETWORK_ERROR."""
    response, calls = _run_with_fakes(
        _transport(),
        "Files.GetDirectory",
        [URLError(OSError("Connection refused")), URLError(OSError("Connection refused"))],
    )
    assert len(calls) == 2
    assert response.error == "connection error: Connection refused"
    assert response.error_type == ErrorType.NETWORK_ERROR


def test_mutating_method_does_not_retry_on_timeout():
    """Player.Open (not in SAFE_READ_METHODS): socket.timeout -> exactly 1
    attempt, typed TIMEOUT error."""
    response, calls = _run_with_fakes(
        _transport(),
        "Player.Open",
        [socket.timeout("timed out")],
    )
    assert len(calls) == 1
    assert response.error == "request timeout"
    assert response.error_type == ErrorType.TIMEOUT


def test_mutating_method_does_not_retry_on_connection_error():
    """Player.Open: URLError -> exactly 1 attempt, typed NETWORK_ERROR."""
    response, calls = _run_with_fakes(
        _transport(),
        "Player.Open",
        [URLError(OSError("Connection refused"))],
    )
    assert len(calls) == 1
    assert response.error == "connection error: Connection refused"
    assert response.error_type == ErrorType.NETWORK_ERROR
