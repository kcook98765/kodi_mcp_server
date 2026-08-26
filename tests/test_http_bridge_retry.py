"""Regression tests for HttpBridgeClient._retry_wrapper.

The retry wrapper is documented to retry transient transport failures
(timeout / connection error) with backoff. The blocking ``_make_request``
converts ``URLError`` / ``socket.timeout`` into typed ``ResponseMessage``s
instead of raising, so retryability must be detected from the returned
``error_type`` — not by catching exceptions. These tests pin that contract:
a transient failure is retried once and recovers, and a persistent failure is
bounded to a single retry before the transport error is surfaced.

These are written as *synchronous* pytest tests that drive the coroutine on a
fresh ``asyncio.new_event_loop()`` without installing it as the thread's
current loop. Using ``@pytest.mark.asyncio`` would clear the main thread's
current event-loop slot on teardown, which breaks the legacy
``asyncio.get_event_loop().run_until_complete()`` pattern used by
``tests/test_http_errors.py`` under Python 3.13 when this file is collected
first. The fresh-loop approach leaves that slot untouched.
"""
import asyncio

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient


def _run(coro):
    """Run *coro* on a fresh loop without installing it as the current loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeBlocking:
    """Self-contained stand-in for the blocking ``_make_request``.

    Returns scripted ``ResponseMessage``s in order and records call count.
    No network is touched.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def __call__(self, method, path, query=None, payload=None, headers=None):
        self.calls += 1
        return self._responses.pop(0)


def _ok():
    return ResponseMessage(
        request_id="bridge-request", result={"ok": True}, error=None, error_type=None
    )


def _net_error():
    return ResponseMessage(
        request_id="bridge-request",
        result=None,
        error="connection error: refused",
        error_type=ErrorType.NETWORK_ERROR,
    )


def test_retry_recovers_from_transient_network_error():
    """A transient network error on the first attempt is retried once and recovers."""
    client = HttpBridgeClient("http://127.0.0.1:9")
    fake = _FakeBlocking([_net_error(), _ok()])
    client._make_request = fake

    result = _run(client.get_health())

    assert fake.calls == 2  # exactly one retry
    assert result.result == {"ok": True}
    assert result.error is None


def test_retry_is_bounded_and_surfaces_persistent_error():
    """After the allowed retry is exhausted, the transport error is surfaced."""
    client = HttpBridgeClient("http://127.0.0.1:9")
    fake = _FakeBlocking([_net_error(), _net_error()])
    client._make_request = fake

    result = _run(client.get_health())

    assert fake.calls == 2  # initial + one retry, no more
    assert result.error == "connection error: refused"
    assert result.error_type == ErrorType.NETWORK_ERROR
