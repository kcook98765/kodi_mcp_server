"""Direct tests for ``kodi_mcp_server.transport.websocket_notifications``.

Covers the highest-value uncovered behaviors of
``WebSocketNotificationProbe.listen_with_trigger``: the receive loop's
**bounded termination** on a silent socket (the ``asyncio.wait_for`` +
``except asyncio.TimeoutError`` guarantee), the **full-sample success
dispatch** when the socket delivers the requested number of messages in
time, and the **trigger path** itself — a trigger that *fails* (returns an
error ``ResponseMessage``, as its real caller ``run_addon_and_report`` does)
must stay represented separately from WebSocket connection failure, while a
trigger that *succeeds* must be preserved under ``trigger_result`` without
disturbing a healthy collection.

NOTE: these are deliberately *synchronous* pytest tests that drive the probe's
coroutine with an explicit ``asyncio.new_event_loop()`` + ``run_until_complete``
instead of ``@pytest.mark.asyncio`` or ``asyncio.run``. Both of those set (and
then clear) the main thread's "current event loop" slot; on Python 3.13 that
permanently breaks the legacy ``asyncio.get_event_loop()`` auto-create pattern
still used by tests/test_http_errors.py. Creating an explicit loop that is
never set as the thread's current loop leaves that slot untouched, so the full
suite stays green regardless of test-file ordering.

The fake WebSocket below is a minimal ``recv()`` awaitable: it yields queued
messages and then stays pending forever. Production's
``asyncio.wait_for(websocket.recv(), timeout=remaining)`` therefore cancels it
after the listen deadline — exactly the silent-socket case — and the probe must
terminate with whatever messages it collected. No real sleep, no live socket.
"""
import asyncio
import json
import socket
import time

import pytest

from kodi_mcp_server.models.messages import ResponseMessage


class _FakeWebSocket:
    """A silent-socket stand-in: yields pre-queued messages, then hangs.

    ``recv`` is a genuine awaitable (production does
    ``await asyncio.wait_for(websocket.recv(), timeout=remaining)``). Once the
    queue is exhausted it blocks forever; the probe's ``wait_for`` is what
    turns that pending await into a timeout, so termination is driven by the
    real production code path, not by the fake.
    """

    def __init__(self, queued):
        self._queue = list(queued)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def recv(self):
        if self._queue:
            return self._queue.pop(0)
        # Simulate a socket that stops delivering messages: stay pending so
        # the caller's asyncio.wait_for times out.
        await asyncio.Event().wait()


class _ClosingWebSocket:
    """An established socket whose first receive reports a transport close."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def recv(self):
        raise self._exc


async def _run_silent_socket_partial(sample_size, listen_seconds):
    """Drive ``listen_with_trigger`` with a silent socket that delivers fewer
    messages than ``sample_size`` before going quiet.

    Verified:
      1. the probe terminates promptly and boundedly (well under the
         production listen budget — no hang, no real production sleep)
      2. it reports a successful connection (``connected`` True)
      3. it returns the partial sample it actually collected
      4. the ``listen_seconds`` echo reflects the configured budget
      5. no live WebSocket / network is touched (``websockets.connect`` patched)
    """
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    messages = [
        {"method": "Player.OnPlayStart", "params": {"item": {"title": "a"}}},
        {"method": "Player.OnPlayStop", "params": {}},
    ]
    fake_ws = _FakeWebSocket([json.dumps(m) for m in messages])

    def _fake_connect(url, **kwargs):
        # websockets.connect is a *synchronous* factory that returns an async
        # context manager; the fake must mirror that (not return a coroutine).
        assert url == "ws://test:9090/jsonrpc"
        assert kwargs.get("open_timeout") == 10, f"open_timeout: {kwargs.get('open_timeout')!r}"
        return fake_ws

    ws_mod.websockets.connect = _fake_connect

    probe = ws_mod.WebSocketNotificationProbe(tcp_host="test", tcp_port=9090, timeout=10)

    start = time.monotonic()
    response = await asyncio.wait_for(
        probe.listen_with_trigger(sample_size=sample_size, listen_seconds=listen_seconds),
        timeout=20.0,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 15.0, (
        f"probe took {elapsed:.2f}s to terminate; expected bounded termination"
    )

    result = response.to_dict()
    assert response.error is None, f"probe reported an error: {response.error!r}"
    body = result["result"]
    assert body["connected"] is True, f"connected: {body['connected']!r}"
    assert body["websocket_url"] == "ws://test:9090/jsonrpc"
    assert body["messages"] == messages, f"messages: {body['messages']!r}"
    assert body["message_count"] == len(messages), f"message_count: {body['message_count']!r}"
    assert body["listen_seconds"] == listen_seconds
    assert body["event_trigger_used"] is None


def test_listen_terminates_boundedly_on_silent_socket(monkeypatch):
    """A socket that delivers fewer messages than the sample size and then
    goes quiet must terminate the probe at the listen deadline, returning the
    partial sample as a successful (non-error) response."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_silent_socket_partial(sample_size=3, listen_seconds=2))
    finally:
        loop.close()


async def _run_full_sample_dispatch():
    """Drive ``listen_with_trigger`` with a socket that delivers the full
    requested sample immediately.

    Verified:
      1. the probe terminates promptly (no unnecessary listen-deadline wait)
      2. it reports a successful connection
      3. it returns the complete sample in order, with the matching count
      4. no live WebSocket / network is touched (``websockets.connect`` patched)
    """
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    sample_size = 3
    messages = [
        {"method": f"Player.OnPlayStart_{i}", "params": {"i": i}}
        for i in range(sample_size)
    ]
    fake_ws = _FakeWebSocket([json.dumps(m) for m in messages])

    def _fake_connect(url, **kwargs):
        # websockets.connect is a *synchronous* factory that returns an async
        # context manager; the fake must mirror that (not return a coroutine).
        return fake_ws

    ws_mod.websockets.connect = _fake_connect

    probe = ws_mod.WebSocketNotificationProbe(tcp_host="test", tcp_port=9090, timeout=10)

    start = time.monotonic()
    response = await asyncio.wait_for(
        probe.listen_with_trigger(sample_size=sample_size, listen_seconds=5),
        timeout=20.0,
    )
    elapsed = time.monotonic() - start

    # The full sample is delivered immediately, so the probe should return
    # well before the listen deadline — no need to wait out the budget.
    assert elapsed < 4.0, f"probe took {elapsed:.2f}s; full sample should return quickly"

    result = response.to_dict()
    assert response.error is None, f"probe reported an error: {response.error!r}"
    body = result["result"]
    assert body["connected"] is True
    assert body["messages"] == messages, f"messages: {body['messages']!r}"
    assert body["message_count"] == sample_size
    assert body["listen_seconds"] == 5


def test_listen_returns_full_sample_when_delivered_in_time(monkeypatch):
    """When the socket delivers the full sample before the listen deadline, the
    probe returns that complete sample (in order) as a successful response."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_full_sample_dispatch())
    finally:
        loop.close()


async def _run_trigger_error_preserved_separately(sample_size, listen_seconds):
    """Drive ``listen_with_trigger`` with a healthy socket plus a trigger that
    returns an *error* ResponseMessage (its real caller,
    ``run_addon_and_report``, reports failures this way instead of raising).

    Verified:
      1. the probe still reports a successful WebSocket connection
         (``connected`` True) — trigger failure is not misclassified as a
         connection/receive failure
      2. the trigger result is preserved *separately*:
         ``trigger_result.error`` carries the trigger's error, and the
         probe-level ``error`` field stays None (no ``likely_cause``)
      3. notifications received *after* the failed trigger are still
         collected (ordering: trigger runs before the collection loop)
      4. the probe terminates boundedly (hard 20s outer timeout; the
         production ``await asyncio.sleep(1)`` is monkeypatched to a no-op
         fake so no real second is spent)
      5. no live WebSocket / network is touched (``websockets.connect`` patched)
    """
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    trigger_error = "Addons.ExecuteAddon failed: addon not found"
    trigger_response = ResponseMessage(
        request_id="trigger-fake",
        result=None,
        error=trigger_error,
    )
    calls = []

    async def _failing_trigger():
        calls.append("trigger")
        return trigger_response

    # Queue one pre-existing notification, then one delivered after the
    # trigger executes: proves the failed trigger did not abort collection.
    messages = [
        {"method": "Player.OnPlayStart", "params": {"i": 0}},
        {"method": "Player.OnPlayStop", "params": {"i": 1}},
    ]
    fake_ws = _FakeWebSocket([json.dumps(m) for m in messages])

    def _fake_connect(url, **kwargs):
        # websockets.connect is a *synchronous* factory that returns an async
        # context manager; the fake must mirror that (not return a coroutine).
        return fake_ws

    ws_mod.websockets.connect = _fake_connect

    # Seam around the intentional pre-trigger await: record, don't wait.
    # Production timing is left unchanged. Capture the original *before*
    # patching so the fake does not recurse into itself.
    sleep_calls = []
    real_sleep = ws_mod.asyncio.sleep

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatcher = ws_mod.asyncio
    monkeypatcher.sleep = _fake_sleep

    try:
        probe = ws_mod.WebSocketNotificationProbe(
            tcp_host="test", tcp_port=9090, timeout=10
        )
        start = time.monotonic()
        response = await asyncio.wait_for(
            probe.listen_with_trigger(
                sample_size=sample_size,
                listen_seconds=listen_seconds,
                trigger=_failing_trigger,
                trigger_name="validate_kodi_notifications",
            ),
            timeout=20.0,
        )
        elapsed = time.monotonic() - start
    finally:
        monkeypatcher.sleep = real_sleep

    assert sleep_calls == [1], f"pre-trigger sleep seam calls: {sleep_calls!r}"
    assert elapsed < 5.0, (
        f"probe took {elapsed:.2f}s; fake sleep means it should bound fast"
    )

    assert calls == ["trigger"], "trigger must execute exactly once"

    # A broken implementation that misclassifies the trigger failure as a
    # WebSocket problem would land here instead:
    assert response.error is None, (
        f"probe-level error must stay None on trigger failure, got: "
        f"{response.error!r}"
    )
    body = response.to_dict()["result"]
    assert body["connected"] is True, f"connected: {body['connected']!r}"
    assert "likely_cause" not in body, (
        f"no connection-failure classification expected: {body!r}"
    )
    assert body["event_trigger_used"] == "validate_kodi_notifications"
    assert body["trigger_result"] == {
        "result": None,
        "error": trigger_error,
    }, f"trigger_result: {body['trigger_result']!r}"
    # Ordering: the post-trigger notification was still collected.
    assert body["messages"] == messages, f"messages: {body['messages']!r}"
    assert body["message_count"] == len(messages)
    assert body["listen_seconds"] == listen_seconds


def test_trigger_failure_stays_separate_from_connection_failure():
    """A trigger that returns an unsuccessful ResponseMessage must not flip the
    probe into a WebSocket connection-failure report: the connection stays
    healthy, the trigger error is preserved under ``trigger_result``, and the
    probe-level error stays None."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _run_trigger_error_preserved_separately(sample_size=2, listen_seconds=5)
        )
    finally:
        loop.close()


async def _run_trigger_success_preserved(sample_size, listen_seconds):
    """Drive ``listen_with_trigger`` with a healthy socket plus a trigger that
    returns a *successful* ResponseMessage (``result`` populated, ``error``
    None) — the other half of the trigger-result contract.

    Verified:
      1. the trigger's success payload is preserved under
         ``trigger_result`` (``result`` set, ``error`` None)
      2. the probe-level ``error`` stays None and the connection is healthy
         (``connected`` True, no ``likely_cause``)
      3. the full sample is still collected after the successful trigger
      4. the probe terminates boundedly (hard 20s outer timeout; the
         production pre-trigger sleep is monkeypatched to a no-op fake so no
         real second is spent)
      5. no live WebSocket / network is touched (``websockets.connect`` patched)
    """
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    trigger_result_payload = {"addonid": "script.viewer.sprites_zoom", "ok": True}
    trigger_response = ResponseMessage(
        request_id="trigger-fake",
        result=trigger_result_payload,
        error=None,
    )
    calls = []

    async def _succeeding_trigger():
        calls.append("trigger")
        return trigger_response

    # Queue the full requested sample; delivery resumes after the trigger.
    messages = [
        {"method": f"Player.OnPlayStart_{i}", "params": {"i": i}}
        for i in range(sample_size)
    ]
    fake_ws = _FakeWebSocket([json.dumps(m) for m in messages])

    def _fake_connect(url, **kwargs):
        # websockets.connect is a *synchronous* factory that returns an async
        # context manager; the fake must mirror that (not return a coroutine).
        return fake_ws

    ws_mod.websockets.connect = _fake_connect

    # Seam around the intentional pre-trigger await: record, don't wait.
    # Production timing is left unchanged. Capture the original *before*
    # patching so the fake does not recurse into itself.
    sleep_calls = []
    real_sleep = ws_mod.asyncio.sleep

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatcher = ws_mod.asyncio
    monkeypatcher.sleep = _fake_sleep

    try:
        probe = ws_mod.WebSocketNotificationProbe(
            tcp_host="test", tcp_port=9090, timeout=10
        )
        start = time.monotonic()
        response = await asyncio.wait_for(
            probe.listen_with_trigger(
                sample_size=sample_size,
                listen_seconds=listen_seconds,
                trigger=_succeeding_trigger,
                trigger_name="run_addon_and_report:script.viewer.sprites_zoom",
            ),
            timeout=20.0,
        )
        elapsed = time.monotonic() - start
    finally:
        monkeypatcher.sleep = real_sleep

    assert sleep_calls == [1], f"pre-trigger sleep seam calls: {sleep_calls!r}"
    assert elapsed < 5.0, (
        f"probe took {elapsed:.2f}s; fake sleep means it should bound fast"
    )
    assert calls == ["trigger"], "trigger must execute exactly once"

    assert response.error is None, f"probe reported an error: {response.error!r}"
    body = response.to_dict()["result"]
    assert body["connected"] is True, f"connected: {body['connected']!r}"
    assert "likely_cause" not in body, f"no likely_cause expected: {body!r}"
    assert body["event_trigger_used"] == (
        "run_addon_and_report:script.viewer.sprites_zoom"
    )
    assert body["trigger_result"] == {
        "result": trigger_result_payload,
        "error": None,
    }, f"trigger_result: {body['trigger_result']!r}"
    # A successful trigger must not disturb a healthy full-sample collection.
    assert body["messages"] == messages, f"messages: {body['messages']!r}"
    assert body["message_count"] == sample_size
    assert body["listen_seconds"] == listen_seconds


async def _run_malformed_frame_skipped(sample_size, listen_seconds):
    """Drive ``listen_with_trigger`` with a socket that delivers a
    non-JSON frame between two valid notifications, then goes quiet.

    Verified:
      1. a malformed frame is *skipped*, not treated as a failure — the
         probe-level ``error`` stays None and the connection stays healthy
         (``connected`` True, no ``likely_cause``)
      2. valid notifications before and after the bad frame are still
         collected, in order
      3. the probe terminates boundedly (hard 20s outer timeout; no live
         WebSocket / network — ``websockets.connect`` patched)
    """
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    messages = [
        {"method": "Player.OnPlayStart", "params": {"i": 0}},
        {"method": "Player.OnPlayStop", "params": {"i": 1}},
    ]
    bad_frame = "not-json-plain-text"  # valid text, invalid JSON
    frames = [json.dumps(messages[0]), bad_frame, json.dumps(messages[1])]
    fake_ws = _FakeWebSocket(frames)

    def _fake_connect(url, **kwargs):
        # websockets.connect is a *synchronous* factory that returns an async
        # context manager; the fake must mirror that (not return a coroutine).
        return fake_ws

    ws_mod.websockets.connect = _fake_connect

    probe = ws_mod.WebSocketNotificationProbe(tcp_host="test", tcp_port=9090, timeout=10)

    start = time.monotonic()
    response = await asyncio.wait_for(
        probe.listen_with_trigger(sample_size=sample_size, listen_seconds=listen_seconds),
        timeout=20.0,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 15.0, f"probe took {elapsed:.2f}s to terminate; expected bounded"

    # A broken implementation that misclassifies the malformed frame as a
    # WebSocket problem would land here instead:
    assert response.error is None, (
        f"probe-level error must stay None on a malformed frame, got: "
        f"{response.error!r}"
    )
    body = response.to_dict()["result"]
    assert body["connected"] is True, f"connected: {body['connected']!r}"
    assert "likely_cause" not in body, (
        f"no connection-failure classification expected: {body!r}"
    )
    # The malformed frame was skipped; both valid frames were collected in order.
    assert body["messages"] == messages, f"messages: {body['messages']!r}"
    assert body["message_count"] == len(messages)
    assert body["listen_seconds"] == listen_seconds


def test_malformed_frame_is_skipped_without_connection_failure():
    """A WebSocket frame that is not valid JSON must be skipped at the parse
    boundary: the connection stays healthy, no probe-level error is raised,
    and the valid notifications around the bad frame are still collected."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_malformed_frame_skipped(sample_size=3, listen_seconds=2))
    finally:
        loop.close()


def test_trigger_success_preserved_without_disturbing_collection():
    """A trigger that returns a successful ResponseMessage must be preserved
    under ``trigger_result`` (result set, error None) while the probe keeps
    reporting a healthy connection and the full collected sample."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _run_trigger_success_preserved(sample_size=3, listen_seconds=5)
        )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# _classify_error: direct, deterministic tests of the connection-failure
# diagnostic. The probe's ``except Exception`` handler passes ``str(exc)`` to
# ``_classify_error``; these cases pin what each REAL exception category that
# ``websockets.connect`` can raise maps to, using deterministic exception
# objects (no live socket).
#
# Why exception *objects* rather than string literals: ``asyncio.TimeoutError``
# (== ``TimeoutError`` on Python 3.13) has an EMPTY ``str()`` and
# ``socket.gaierror`` (DNS) carries no stable distinguishing text, so those two
# categories can only be distinguished by TYPE — which is exactly the defect
# under test (string-matching today cannot tell a timeout from a bad port).
# The refused/401 cases are regression guards for the branches that already
# classify correctly.
# ---------------------------------------------------------------------------

_TCP_GUIDANCE = "Kodi TCP control not enabled or wrong TCP port"
_AUTH_GUIDANCE = "auth/handshake mismatch"


def _make_invalid_status(status_code: int):
    """Deterministically build a real ``websockets.InvalidStatus`` (handshake
    rejection, e.g. HTTP 401/403) without any live connection."""
    from websockets import http11, exceptions
    from websockets.datastructures import Headers

    response = http11.Response(status_code, "", Headers(), [])
    return exceptions.InvalidStatus(response)


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_cause"),
    [
        pytest.param(
            ConnectionRefusedError(111, "Connect call failed ('172.27.0.1', 9090)"),
            "connection_refused",
            _TCP_GUIDANCE,
            id="connection-refused",
        ),
        pytest.param(
            asyncio.TimeoutError(),
            "connection_timeout",
            "connection to Kodi timed out",
            id="open-timeout",
        ),
        pytest.param(
            socket.gaierror(-2, "Name or service not known"),
            "name_resolution_failure",
            "DNS/name resolution failure",
            id="dns-failure",
        ),
        pytest.param(
            _make_invalid_status(401),
            "handshake_auth_failure",
            _AUTH_GUIDANCE,
            id="auth-401-handshake",
        ),
        pytest.param(
            _make_invalid_status(500),
            "handshake_failure",
            "WebSocket handshake failed",
            id="http-500-handshake",
        ),
        pytest.param(
            ConnectionResetError(104, "Connection reset by peer"),
            "network_failure",
            "WebSocket network transport failed",
            id="connection-reset",
        ),
        pytest.param(
            RuntimeError("opaque failure"),
            "transport_failure",
            "WebSocket transport failed for an unknown reason",
            id="unknown-transport-failure",
        ),
    ],
)
def test_classify_error_maps_real_failure_categories(exc, expected_code, expected_cause):
    """Each distinct WebSocket failure category the probe can encounter must
    map to operator guidance that names THAT category.

    The two new assertions (timeout, dns) are RED against the string-only
    implementation: ``TimeoutError`` has an empty ``str()`` and ``gaierror``
    matches no substring, so today both fall through to the generic
    "wrong TCP port" guidance — misleading for both.
    """
    from kodi_mcp_server.transport.websocket_notifications import (
        WebSocketNotificationProbe,
    )

    probe = WebSocketNotificationProbe(tcp_host="test", tcp_port=9090)
    assert probe._diagnostic_code(exc) == expected_code
    cause = probe._classify_error(exc)
    assert expected_cause in cause, (
        f"{type(exc).__name__} (str={str(exc)!r}) classified as {cause!r}; "
        f"expected guidance containing {expected_cause!r}"
    )


def test_established_connection_closure_is_truthful_interruption(monkeypatch):
    """A lost established socket is not evidence of a disabled service or bad port."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod
    from websockets.exceptions import ConnectionClosedError

    monkeypatch.setattr(
        ws_mod.websockets,
        "connect",
        lambda url, **kwargs: _ClosingWebSocket(ConnectionClosedError(None, None)),
    )
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="test", tcp_port=9090)

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(probe.listen(sample_size=1, listen_seconds=2))
    finally:
        loop.close()

    assert response.error == "no close frame received or sent"
    assert response.result is not None
    assert response.result["connected"] is False
    assert response.result["diagnostic_code"] == "connection_interrupted"
    guidance = response.result["likely_cause"].lower()
    assert "route/target change" in guidance
    assert "disabled" not in guidance
    assert "wrong" not in guidance
    assert "intentional" not in guidance


def test_diagnostic_redacts_websocket_credentials_and_query(monkeypatch):
    """The real endpoint is used to connect but secrets never enter result diagnostics."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod
    from websockets.exceptions import ConnectionClosedError

    private_url = "ws://operator:secret@example.test:9090/jsonrpc?token=private"
    observed_urls = []

    def fake_connect(url, **kwargs):
        observed_urls.append(url)
        return _ClosingWebSocket(ConnectionClosedError(None, None))

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe = ws_mod.WebSocketNotificationProbe(
        tcp_host="unused",
        websocket_url=private_url,
    )

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(probe.listen(sample_size=1, listen_seconds=2))
    finally:
        loop.close()

    assert observed_urls == [private_url]
    assert response.result is not None
    assert response.result["websocket_url"] == "ws://example.test:9090/jsonrpc"
    serialized = json.dumps(response.to_dict())
    assert "operator" not in serialized
    assert "secret" not in serialized
    assert "private" not in serialized


def test_fresh_sample_recovers_after_interrupted_connection(monkeypatch):
    """An interrupted call retains no socket state that can poison the next sample."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod
    from websockets.exceptions import ConnectionClosedError

    expected_message = {"method": "Player.OnPlay", "params": {}}
    connections = [
        _ClosingWebSocket(ConnectionClosedError(None, None)),
        _FakeWebSocket([json.dumps(expected_message)]),
    ]
    monkeypatch.setattr(
        ws_mod.websockets,
        "connect",
        lambda url, **kwargs: connections.pop(0),
    )
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="test", tcp_port=9090)

    loop = asyncio.new_event_loop()
    try:
        interrupted = loop.run_until_complete(probe.listen(sample_size=1, listen_seconds=2))
        recovered = loop.run_until_complete(probe.listen(sample_size=1, listen_seconds=2))
    finally:
        loop.close()

    assert interrupted.error == "no close frame received or sent"
    assert interrupted.result is not None
    assert interrupted.result["diagnostic_code"] == "connection_interrupted"
    assert recovered.error is None
    assert recovered.result is not None
    assert recovered.result["connected"] is True
    assert recovered.result["messages"] == [expected_message]
    assert "diagnostic_code" not in recovered.result


def test_repeated_refusal_remains_visible_without_false_recovery(monkeypatch):
    """A persistently unavailable route stays failed on every fresh attempt."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    def refuse_connection(url, **kwargs):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(ws_mod.websockets, "connect", refuse_connection)
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="test", tcp_port=9090)

    loop = asyncio.new_event_loop()
    try:
        responses = [
            loop.run_until_complete(probe.listen(sample_size=1, listen_seconds=2))
            for _ in range(2)
        ]
    finally:
        loop.close()

    for response in responses:
        assert response.error == "[Errno 111] Connection refused"
        assert response.result is not None
        assert response.result["connected"] is False
        assert response.result["diagnostic_code"] == "connection_refused"
        assert response.result["likely_cause"] == _TCP_GUIDANCE
        assert "recovered" not in response.result


def _redirect_status(*locations: str):
    from websockets import http11, exceptions
    from websockets.datastructures import Headers

    headers = Headers()
    for location in locations:
        headers["Location"] = location
    response = http11.Response(302, "Found", headers, b"")
    return exceptions.InvalidStatus(response)


class _RaisingConnect:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _listen_target_bound_with_connect_error(
    monkeypatch,
    exc,
    *,
    websocket_url="ws://origin.example:9090/start",
):
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    monkeypatch.setattr(
        ws_mod.websockets,
        "connect",
        lambda url, **kwargs: _RaisingConnect(exc),
    )
    probe = ws_mod.WebSocketNotificationProbe(
        tcp_host="unused",
        websocket_url=websocket_url,
    )
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(probe.listen_target_bound())
    finally:
        loop.close()


@pytest.mark.parametrize(
    "graph_shape",
    ["cause", "context", "split-branches", "multi-level", "cycle"],
)
def test_target_bound_redirect_searches_complete_exception_graph(
    monkeypatch,
    graph_shape,
):
    redirected = "ws://other.example:9191/graph-secret?token=hidden"
    redirect = _redirect_status(redirected)
    outer = RuntimeError("outer leaked ws://outer-secret.example/path")
    if graph_shape == "cause":
        outer.__cause__ = redirect
    elif graph_shape == "context":
        outer.__context__ = redirect
    elif graph_shape == "split-branches":
        outer.__cause__ = ValueError("unrelated cause")
        outer.__context__ = redirect
    elif graph_shape == "multi-level":
        wrapper = ValueError("middle wrapper")
        wrapper.__context__ = redirect
        outer.__cause__ = wrapper
    else:
        wrapper = ValueError("cycle wrapper")
        outer.__context__ = wrapper
        wrapper.__context__ = outer
        wrapper.__cause__ = redirect

    response = _listen_target_bound_with_connect_error(monkeypatch, outer)

    assert response.error == (
        "cross-origin redirect rejected for target-bound WebSocket endpoint"
    )
    assert response.result is not None
    assert response.result["diagnostic_code"] == "cross_origin_redirect"
    serialized = json.dumps(response.to_dict())
    assert "other.example" not in serialized
    assert "graph-secret" not in serialized
    assert "outer-secret" not in serialized


def test_target_bound_exception_graph_cycle_without_redirect_terminates(monkeypatch):
    outer = RuntimeError("ordinary transport failure")
    wrapper = ValueError("cycle wrapper")
    outer.__context__ = wrapper
    wrapper.__cause__ = outer

    response = _listen_target_bound_with_connect_error(monkeypatch, outer)

    assert response.error == "ordinary transport failure"
    assert response.result is not None
    assert response.result["diagnostic_code"] == "transport_failure"


def test_target_bound_exception_graph_traversal_limit_is_destination_free(monkeypatch):
    outer = RuntimeError("outer-limit-secret ws://destination-secret.example/path")
    current = outer
    for index in range(65):
        wrapped = RuntimeError(f"wrapped-limit-secret-{index}")
        current.__cause__ = wrapped
        current = wrapped

    response = _listen_target_bound_with_connect_error(monkeypatch, outer)

    assert response.error == "WebSocket redirect failed for target-bound endpoint"
    assert response.result is not None
    assert response.result["diagnostic_code"] == "redirect_failure"
    serialized = json.dumps(response.to_dict())
    assert "outer-limit-secret" not in serialized
    assert "destination-secret" not in serialized
    assert "wrapped-limit-secret" not in serialized


@pytest.mark.parametrize(
    ("locations", "forbidden"),
    [
        pytest.param((), "outer-secret", id="no-location"),
        pytest.param(
            (
                "ws://first-secret.example/path",
                "ws://second-secret.example/path",
            ),
            "secret.example",
            id="duplicate-location",
        ),
        pytest.param(("ws://[malformed-location",), "malformed-location", id="malformed-location"),
    ],
)
def test_target_bound_malformed_redirect_headers_are_destination_free(
    monkeypatch,
    locations,
    forbidden,
):
    redirect = _redirect_status(*locations)
    outer = RuntimeError("outer-secret redirect wrapper")
    outer.__cause__ = redirect

    response = _listen_target_bound_with_connect_error(monkeypatch, outer)

    assert response.error == "WebSocket redirect failed for target-bound endpoint"
    assert response.result is not None
    assert response.result["diagnostic_code"] == "redirect_failure"
    serialized = json.dumps(response.to_dict())
    assert forbidden not in serialized
    assert "outer-secret" not in serialized


def test_target_bound_public_same_origin_redirect_delivers_event(monkeypatch):
    """The public client follows a real local same-origin handshake redirect."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod
    from websockets.asyncio.client import connect as public_connect
    from websockets.asyncio.server import serve

    expected = {"method": "Player.OnPlay", "params": {"item": {"id": 7}}}
    request_paths = []
    monkeypatch.setattr(ws_mod.websockets, "connect", public_connect)

    async def handler(websocket):
        await websocket.send(json.dumps(expected))

    async def process_request(connection, request):
        request_paths.append(request.path)
        if request.path == "/start":
            response = connection.respond(302, "redirect")
            response.headers["Location"] = "/final?source=redirect"
            return response
        return None

    async def run_test():
        server = await serve(
            handler,
            "127.0.0.1",
            0,
            process_request=process_request,
        )
        port = next(iter(server.sockets)).getsockname()[1]
        probe = ws_mod.WebSocketNotificationProbe(
            tcp_host="unused",
            websocket_url=f"ws://127.0.0.1:{port}/start",
        )
        try:
            return await probe.listen_target_bound(sample_size=1, listen_seconds=2)
        finally:
            server.close()
            await server.wait_closed()

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(run_test())
    finally:
        loop.close()

    assert request_paths == ["/start", "/final?source=redirect"]
    assert response.error is None
    assert response.result is not None
    assert response.result["messages"] == [expected]


@pytest.mark.parametrize("redirect_kind", ["cross-host", "cross-port", "ws-to-wss"])
def test_target_bound_public_redirect_rejects_origin_change(
    monkeypatch,
    redirect_kind,
):
    """The public client rejects local redirect handshakes before destination use."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod
    from websockets.asyncio.client import connect as public_connect
    from websockets.asyncio.server import serve

    destination_connections = []
    redirect_location = ""
    monkeypatch.setattr(ws_mod.websockets, "connect", public_connect)

    async def destination_handler(websocket):
        destination_connections.append(True)
        await websocket.send(json.dumps({"method": "forbidden"}))

    async def source_handler(websocket):
        pytest.fail("redirect source must not complete a WebSocket handshake")

    async def process_request(connection, request):
        response = connection.respond(302, "redirect")
        response.headers["Location"] = redirect_location
        return response

    async def run_test():
        nonlocal redirect_location
        destination_server = await serve(destination_handler, "127.0.0.1", 0)
        destination_port = next(iter(destination_server.sockets)).getsockname()[1]
        source_server = await serve(
            source_handler,
            "127.0.0.1",
            0,
            process_request=process_request,
        )
        source_port = next(iter(source_server.sockets)).getsockname()[1]
        if redirect_kind == "cross-host":
            redirect_location = f"ws://localhost:{source_port}/redirect-secret"
        elif redirect_kind == "cross-port":
            redirect_location = (
                f"ws://127.0.0.1:{destination_port}/redirect-secret"
            )
        else:
            redirect_location = f"wss://127.0.0.1:{source_port}/redirect-secret"
        probe = ws_mod.WebSocketNotificationProbe(
            tcp_host="unused",
            websocket_url=f"ws://127.0.0.1:{source_port}/start",
        )
        try:
            return await probe.listen_target_bound(sample_size=1, listen_seconds=2)
        finally:
            source_server.close()
            destination_server.close()
            await source_server.wait_closed()
            await destination_server.wait_closed()

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(run_test())
    finally:
        loop.close()

    assert destination_connections == []
    assert response.error == (
        "cross-origin redirect rejected for target-bound WebSocket endpoint"
    )
    assert response.result is not None
    assert response.result["diagnostic_code"] == "cross_origin_redirect"
    assert "redirect-secret" not in json.dumps(response.to_dict())


def test_target_bound_structurally_classifies_wss_downgrade(monkeypatch):
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    redirect = _redirect_status(
        "ws://origin.example/redirect-secret?location=downgrade"
    )
    outer = ws_mod.websockets.exceptions.SecurityError("wrapped destination")
    outer.__cause__ = redirect
    response = _listen_target_bound_with_connect_error(
        monkeypatch,
        outer,
        websocket_url="wss://origin.example/start",
    )

    assert response.error == (
        "cross-origin redirect rejected for target-bound WebSocket endpoint"
    )
    assert response.result is not None
    assert response.result["diagnostic_code"] == "cross_origin_redirect"
    serialized = json.dumps(response.to_dict())
    assert "redirect-secret" not in serialized
    assert "wrapped destination" not in serialized


def test_target_bound_listen_disables_implicit_proxy_discovery(monkeypatch):
    """Strict sampling connects directly to the authored endpoint."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeWebSocket([json.dumps({"method": "System.OnQuit"})])

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="target.example", tcp_port=9191)

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(
            probe.listen_target_bound(sample_size=1, listen_seconds=2)
        )
    finally:
        loop.close()

    assert response.error is None
    assert calls == [
        (
            "ws://target.example:9191/jsonrpc",
            {
                "open_timeout": 10,
                "proxy": None,
                "host": "target.example",
                "port": 9191,
            },
        )
    ]


def test_target_bound_connection_failure_is_attempted_once(monkeypatch):
    """One failed target-bound call is one connection attempt, without retry."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    calls = []

    def refuse(url, **kwargs):
        calls.append((url, kwargs))
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(ws_mod.websockets, "connect", refuse)
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="target.example")

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(probe.listen_target_bound())
    finally:
        loop.close()

    assert len(calls) == 1
    assert response.error == "[Errno 111] Connection refused"
    assert response.result is not None
    assert response.result["diagnostic_code"] == "connection_refused"


class _FrameSequenceWebSocket(_FakeWebSocket):
    """Yield frames and raise queued exceptions in receive order."""

    async def recv(self):
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        await asyncio.Event().wait()


@pytest.mark.parametrize("listen_method", ["listen", "listen_target_bound"])
def test_partial_stream_failure_does_not_reconnect_or_return_partial_messages(
    monkeypatch,
    listen_method,
):
    """A mid-window close keeps the existing all-or-failure stream policy."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod
    from websockets.exceptions import ConnectionClosedError

    first = {"method": "Player.OnPlay", "params": {"item": {"id": 1}}}
    forbidden_second = {"method": "Player.OnStop", "params": {"item": {"id": 2}}}
    connections = [
        _FrameSequenceWebSocket(
            [json.dumps(first), ConnectionClosedError(None, None)]
        ),
        _FakeWebSocket([json.dumps(forbidden_second)]),
    ]
    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        return connections.pop(0)

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="target.example")

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(
            getattr(probe, listen_method)(sample_size=2, listen_seconds=2)
        )
    finally:
        loop.close()

    assert len(calls) == 1
    assert response.error == "no close frame received or sent"
    assert response.result is not None
    assert response.result["connected"] is False
    assert response.result["messages"] == []
    assert response.result["diagnostic_code"] == "connection_interrupted"
    assert forbidden_second["method"] not in json.dumps(response.to_dict())


class _OpeningGateWebSocket(_FakeWebSocket):
    def __init__(self, queued, gate):
        super().__init__(queued)
        self._gate = gate

    async def __aenter__(self):
        await self._gate.wait()
        return self


def test_concurrent_target_bound_calls_on_cached_probe_use_distinct_websockets(monkeypatch):
    """A cached probe shares endpoint config, never a live socket or receive buffer."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    gate = asyncio.Event()
    sockets = []

    def fake_connect(url, **kwargs):
        index = len(sockets)
        websocket = _OpeningGateWebSocket(
            [json.dumps({"method": f"Event.{index}"})], gate
        )
        sockets.append(websocket)
        if len(sockets) == 2:
            gate.set()
        return websocket

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="target.example")

    async def run_calls():
        return await asyncio.gather(
            probe.listen_target_bound(sample_size=1, listen_seconds=2),
            probe.listen_target_bound(sample_size=1, listen_seconds=2),
        )

    loop = asyncio.new_event_loop()
    try:
        responses = loop.run_until_complete(run_calls())
    finally:
        loop.close()

    assert len(sockets) == 2
    assert sockets[0] is not sockets[1]
    assert [response.result["messages"] for response in responses] == [
        [{"method": "Event.0"}],
        [{"method": "Event.1"}],
    ]


def test_separate_target_bound_probes_cannot_cross_share_connection_state(monkeypatch):
    """Two probe instances retain independent endpoints, sockets, and messages."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    gate = asyncio.Event()
    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs["host"]))
        websocket = _OpeningGateWebSocket(
            [json.dumps({"method": f"Event.{kwargs['host']}"})], gate
        )
        if len(calls) == 2:
            gate.set()
        return websocket

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe_a = ws_mod.WebSocketNotificationProbe(tcp_host="a.example")
    probe_b = ws_mod.WebSocketNotificationProbe(tcp_host="b.example")

    async def run_calls():
        return await asyncio.gather(
            probe_a.listen_target_bound(sample_size=1, listen_seconds=2),
            probe_b.listen_target_bound(sample_size=1, listen_seconds=2),
        )

    loop = asyncio.new_event_loop()
    try:
        response_a, response_b = loop.run_until_complete(run_calls())
    finally:
        loop.close()

    assert calls == [
        ("ws://a.example:9090/jsonrpc", "a.example"),
        ("ws://b.example:9090/jsonrpc", "b.example"),
    ]
    assert response_a.result["messages"] == [{"method": "Event.a.example"}]
    assert response_b.result["messages"] == [{"method": "Event.b.example"}]


@pytest.mark.parametrize(
    ("tcp_host", "websocket_url"),
    [
        pytest.param("", "", id="missing"),
        pytest.param(
            "derived.example",
            "   ",
            id="whitespace-authored-url-does-not-fallback",
        ),
        pytest.param("unused", "ws://user@target.example/jsonrpc", id="userinfo"),
        pytest.param(
            "unused",
            "ws://user:password@target.example/jsonrpc",
            id="username-password",
        ),
        pytest.param("unused", "ws:///jsonrpc", id="missing-host"),
        pytest.param("unused", "http://target.example/jsonrpc", id="unsupported-scheme"),
        pytest.param("unused", "ws://target.example:/jsonrpc", id="empty-port"),
        pytest.param("unused", "ws://target.example:0/jsonrpc", id="zero-port"),
        pytest.param("unused", "ws://target.example:65536/jsonrpc", id="port-too-large"),
        pytest.param(
            "unused",
            "ws://target.example:9090:80/jsonrpc",
            id="malformed-authority",
        ),
        pytest.param(
            "unused",
            r"ws://target.example\evil/jsonrpc",
            id="backslash-ambiguity",
        ),
        pytest.param(
            "unused",
            "ws://target.example/jsonrpc#fragment",
            id="fragment",
        ),
    ],
)
def test_target_bound_unusable_endpoint_fails_before_connect(
    monkeypatch,
    tcp_host,
    websocket_url,
):
    """Strict sampling never falls back when no target endpoint was authored."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    def unexpected_connect(*args, **kwargs):
        pytest.fail("strict endpoint validation must happen before connect")

    monkeypatch.setattr(ws_mod.websockets, "connect", unexpected_connect)
    probe = ws_mod.WebSocketNotificationProbe(
        tcp_host=tcp_host,
        websocket_url=websocket_url,
    )

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(probe.listen_target_bound())
    finally:
        loop.close()

    assert response.error == "target WebSocket endpoint is not configured"
    assert response.result is not None
    assert response.result["connected"] is False
    assert response.result["diagnostic_code"] == "endpoint_not_configured"


@pytest.mark.parametrize(
    ("websocket_url", "expected_host", "expected_port"),
    [
        pytest.param("ws://target.example/jsonrpc", "target.example", 80, id="ws"),
        pytest.param("wss://target.example/jsonrpc", "target.example", 443, id="wss"),
        pytest.param(
            "ws://target.example:9191/events?channel=kodi",
            "target.example",
            9191,
            id="explicit-port-and-query",
        ),
    ],
)
def test_target_bound_valid_authored_endpoint_connects_once(
    monkeypatch,
    websocket_url,
    expected_host,
    expected_port,
):
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeWebSocket([json.dumps({"method": "Player.OnPlay"})])

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe = ws_mod.WebSocketNotificationProbe(
        tcp_host="unused",
        websocket_url=websocket_url,
    )
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(
            probe.listen_target_bound(sample_size=1, listen_seconds=2)
        )
    finally:
        loop.close()

    assert response.error is None
    assert calls == [
        (
            websocket_url,
            {
                "open_timeout": 10,
                "proxy": None,
                "host": expected_host,
                "port": expected_port,
            },
        )
    ]


def test_legacy_listen_connection_arguments_remain_unchanged(monkeypatch):
    """The omitted/default path retains websockets' legacy proxy and redirect defaults."""
    import kodi_mcp_server.transport.websocket_notifications as ws_mod

    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeWebSocket([json.dumps({"method": "Player.OnPlay"})])

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    probe = ws_mod.WebSocketNotificationProbe(tcp_host="legacy.example", timeout=6)

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(probe.listen(sample_size=1, listen_seconds=2))
    finally:
        loop.close()

    assert response.error is None
    assert calls == [
        ("ws://legacy.example:9090/jsonrpc", {"open_timeout": 6})
    ]
