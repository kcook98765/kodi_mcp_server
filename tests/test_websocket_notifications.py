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
    ("exc", "expected_cause"),
    [
        pytest.param(
            ConnectionRefusedError(111, "Connection refused"),
            _TCP_GUIDANCE,
            id="connection-refused",
        ),
        pytest.param(
            asyncio.TimeoutError(),
            "connection to Kodi timed out",
            id="open-timeout",
        ),
        pytest.param(
            socket.gaierror(-2, "Name or service not known"),
            "DNS/name resolution failure",
            id="dns-failure",
        ),
        pytest.param(
            _make_invalid_status(401),
            _AUTH_GUIDANCE,
            id="auth-401-handshake",
        ),
    ],
)
def test_classify_error_maps_real_failure_categories(exc, expected_cause):
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
    cause = probe._classify_error(exc)
    assert cause == expected_cause, (
        f"{type(exc).__name__} (str={str(exc)!r}) classified as {cause!r}; "
        f"expected {expected_cause!r}"
    )
