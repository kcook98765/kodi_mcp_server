"""Regression test: bridge transport must not block the asyncio event loop.

Several ``HttpBridgeClient`` async methods call the blocking ``_make_request``
directly (synchronous ``urllib`` HTTP) inside the coroutine, so a real HTTP call
freezes the event loop for the whole request. This test proves the *async
contract* behaviorally, not by source inspection:

* A background "ticker" coroutine is scheduled on the same loop and bumps a
  counter every ~0.1s.
* The bridge method under test performs a controlled block (a fake
  ``_make_request`` that ``time.sleep``s, standing in for a slow HTTP call).
* If the loop is offloaded correctly, the ticker keeps running during the block
  (many ticks). If the loop is blocked, the ticker cannot run while the blocking
  call holds the loop thread (about one tick, only after the block).

Coverage:
* ``get_file`` — the first method fixed, kept as the representative case.
* a parameterized batch of the remaining simple pass-through methods
  (``write_log_marker``, ``gui_action``, ``gui_screenshot``, ``gui_state``,
  ``ensure_addon_enabled``, ``execute_addon``, ``check_addon_version``,
  ``execute_builtin``, ``refresh_repo``, ``mcp_register``, ``mcp_state``): one
  shared harness proves the class of fix, and each case pins the exact
  ``(method, path, query, payload)`` arguments forwarded to ``_make_request``
  plus exactly one call (no retry semantics added or removed).

Complex methods: ``upload_addon_zip`` (custom urllib + local file I/O) and
``repo_stage_upload`` (custom ``http.client`` streaming flow) have their own
tests in this file; they patch the module-level ``urlopen`` /
``http.client.HTTPConnection`` instead of the shared ``_make_request`` fake.

A control test drives the same harness through ``_retry_wrapper`` (the
retry-protected path that already offloads via ``asyncio.to_thread``) and must
pass today, confirming the harness itself detects offloading.

These are synchronous pytest tests that drive the coroutine on a fresh
``asyncio.new_event_loop()`` WITHOUT installing it as the thread's current loop.
``@pytest.mark.asyncio`` / ``asyncio.run`` clear the main thread's current
event-loop slot on teardown, which breaks the legacy
``asyncio.get_event_loop().run_until_complete()`` pattern used by
``tests/test_http_errors.py`` under Python 3.13.
"""
import asyncio
import io
import json
import time

import pytest

from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient

# The controlled block. Long enough that the ticker reliably fires many times
# when the loop is free (~9 ticks at 0.1s), yet short enough to keep the test
# fast.
BLOCK_SECONDS = 0.9
TICK_PERIOD = 0.1
# Discriminating threshold: offloaded -> ~9 ticks (>= 3 comfortably);
# event-loop blocked -> ~1 tick. 3 sits far from both ends.
MIN_TICKS_FOR_CONCURRENCY = 3
# The parameterized batch uses a shorter block to keep total suite time down
# while still discriminating: offloaded -> ~4-5 ticks, blocked -> ~1 tick.
BATCH_BLOCK_SECONDS = 0.5


def _ticker_harness(coro, tick_period: float = TICK_PERIOD) -> int:
    """Run *coro* on a fresh loop; return how many times a background ticker
    coroutine could run *while* ``coro`` was executing.

    A ticker coroutine bumps a counter every ``tick_period`` seconds. If the
    event loop stays free (the bridge call is offloaded to a thread), the ticker
    fires many times during ``coro``'s block. If ``coro`` blocks the loop, the
    ticker is starved and fires at most once.
    """
    return _run_ticker(coro, tick_period)[0]


def _run_ticker(coro, tick_period: float = TICK_PERIOD):
    """Like ``_ticker_harness`` but returns ``(ticks, result)`` so a test can
    assert on the returned envelope without a second run."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drive_with_ticker(coro, tick_period))
    finally:
        loop.close()


async def _drive_with_ticker(coro, tick_period: float) -> tuple:
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(tick_period)
            ticks += 1

    tick_task = asyncio.ensure_future(ticker())
    try:
        result = await coro
    finally:
        tick_task.cancel()
    return ticks, result



def _make_blocking_make_request(
    payload: dict,
    recorded: list | None = None,
    block_seconds: float = BLOCK_SECONDS,
):
    """A stand-in for the blocking ``_make_request``.

    It blocks the *current* thread for ``block_seconds`` (simulating a slow
    synchronous HTTP call) and then returns the same success envelope the real
    ``_make_request`` returns on a 200 response. When ``recorded`` is given,
    each call's ``(method, path, query, payload)`` arguments are appended to
    it so tests can pin the exact arguments a method must forward.
    """

    def _blocking(method, path, query=None, payload=None, headers=None):
        time.sleep(block_seconds)
        if recorded is not None:
            recorded.append((method, path, query, payload))
        return ResponseMessage(
            request_id="bridge-request",
            result=payload,
            error=None,
            error_type=None,
        )

    return _blocking


def test_get_file_offloads_blocking_http_from_event_loop():
    """Calling ``get_file`` must not freeze the event loop.

    RED: with the current production code, ``get_file`` calls the blocking
    ``_make_request`` synchronously on the loop thread, so the ticker is starved
    and this test fails. GREEN: after offloading the call off the loop, the
    ticker keeps firing and the test passes.
    """
    client = HttpBridgeClient("http://127.0.0.1:9")
    expected = {"path": "/some/file", "content": "abc"}
    client._make_request = _make_blocking_make_request(expected)

    ticks = _ticker_harness(client.get_file("/some/file"))

    assert ticks >= MIN_TICKS_FOR_CONCURRENCY, (
        "event loop was blocked while the bridge call was in progress "
        f"({ticks} ticker tick(s); expected >= {MIN_TICKS_FOR_CONCURRENCY})"
    )


def test_retry_wrapper_offloads_blocking_http_from_event_loop():
    """Control: the retry-protected path already offloads via to_thread.

    Drives the same harness through ``get_health`` (which routes through
    ``_retry_wrapper`` -> ``asyncio.to_thread``). This must PASS today, proving
    the ticker harness detects a non-blocking call. The blocking fake is used so
    the only variable between this test and the ``get_file`` test is whether the
    call is offloaded.
    """
    client = HttpBridgeClient("http://127.0.0.1:9")
    client._make_request = _make_blocking_make_request({"ok": True})

    ticks = _ticker_harness(client.get_health())

    assert ticks >= MIN_TICKS_FOR_CONCURRENCY, (
        f"control failed: event loop blocked under to_thread ({ticks} ticks)"
    )


# Simple pass-through methods that call the blocking ``_make_request``
# directly. Each case pins the exact call arguments the method must forward,
# so the parameterized test proves both the offload (loop stays free) and the
# unchanged protocol shape (method/path/query/payload, exactly one call).
# (method, args, expected (method, path, query, payload))
_SIMPLE_PASS_THROUGH_CASES = [
    (
        "write_log_marker",
        {"message": "hi"},
        ("POST", "/log/marker", None, {"message": "hi"}),
    ),
    (
        "gui_action",
        {"action": "down"},
        ("POST", "/gui/action", None, {"action": "down"}),
    ),
    (
        "gui_screenshot",
        {"include_image": True},
        ("GET", "/gui/screenshot", {"include_image": "true"}, None),
    ),
    (
        "gui_state",
        {},
        ("GET", "/gui/state", None, None),
    ),
    (
        "ensure_addon_enabled",
        {"addonid": "script.foo"},
        ("POST", "/addon/ensure-enabled", {"addonid": "script.foo"}, {}),
    ),
    (
        "execute_addon",
        {"addonid": "script.foo"},
        ("POST", "/addon/execute", {"addonid": "script.foo"}, {}),
    ),
    (
        "check_addon_version",
        {"addonid": "script.foo", "expected_version": "1.0.0"},
        (
            "GET",
            "/addon/version-check",
            {"addonid": "script.foo", "expected_version": "1.0.0"},
            None,
        ),
    ),
    (
        "execute_builtin",
        {"command": "screenshot", "addonid": "script.foo"},
        (
            "POST",
            "/execute_builtin",
            {"command": "screenshot", "addonid": "script.foo"},
            {},
        ),
    ),
    (
        "refresh_repo",
        {},
        ("POST", "/repo/refresh", None, {}),
    ),
    (
        "mcp_register",
        {"payload": {"token": "abc"}},
        ("POST", "/mcp/register", None, {"token": "abc"}),
    ),
    (
        "mcp_state",
        {},
        ("GET", "/mcp/state", None, None),
    ),
]


@pytest.mark.parametrize("method_name, kwargs, expected_call", _SIMPLE_PASS_THROUGH_CASES)
def test_simple_bridge_methods_offload_blocking_http_from_event_loop(
    method_name, kwargs, expected_call
):
    """Every simple pass-through bridge method must not freeze the event loop.

    One shared ticker harness proves the class of fix: the fake
    ``_make_request`` blocks the calling thread for ``BATCH_BLOCK_SECONDS``;
    if the method offloads it via ``asyncio.to_thread`` the ticker keeps
    firing, if the method calls it synchronously on the loop thread the
    ticker is starved. The recorded call arguments also pin that each
    method still forwards its exact (method, path, query, payload) with
    exactly one call — no retry added, protocol unchanged.

    RED before offloading: each case fails with ~0-1 tick(s).
    GREEN after: >= 3 ticks and the pinned arguments match.
    """
    client = HttpBridgeClient("http://127.0.0.1:9")
    recorded: list = []
    client._make_request = _make_blocking_make_request(
        {"ok": True}, recorded=recorded, block_seconds=BATCH_BLOCK_SECONDS
    )

    ticks = _ticker_harness(getattr(client, method_name)(**kwargs))

    assert ticks >= MIN_TICKS_FOR_CONCURRENCY, (
        f"{method_name} blocked the event loop during the bridge call "
        f"({ticks} ticker tick(s); expected >= {MIN_TICKS_FOR_CONCURRENCY})"
    )
    assert recorded == [expected_call], (
        f"{method_name} forwarded unexpected call arguments: "
        f"{recorded!r} (expected {[expected_call]})"
    )


# ---------------------------------------------------------------------------
# upload_addon_zip — complex bridge method (custom urllib + local file I/O)
#
# Unlike the pass-through batch, upload_addon_zip does not go through
# _make_request: it builds its own request (zip body + Content-Type
# application/zip + auth headers), reads the local file, and calls
# urllib_request.urlopen directly. So the fake stands in for the module's
# urlopen, not for _make_request.
# ---------------------------------------------------------------------------


def _patch_urlopen(monkeypatch, fake_urlopen):
    """Patch the module-level ``urlopen`` that upload_addon_zip resolves at
    call time; restored by pytest's ``monkeypatch`` fixture."""
    import kodi_mcp_server.transport.http_bridge as hb_module

    monkeypatch.setattr(hb_module.urllib_request, "urlopen", fake_urlopen)


def _make_slow_urlopen(success_payload: dict):
    """A stand-in for ``urlopen`` that blocks the *calling thread* (simulating
    a slow upload to the bridge) and then behaves like a 200 JSON response.

    Returns ``(fake_urlopen, calls)`` where ``calls`` records, per call, the
    constructed ``urllib_request.Request`` plus the ``timeout`` argument so
    tests can pin the exact request shape (URL, method, body bytes, headers)
    and that the client's own timeout is the one handed to urlopen.
    """
    calls: list = []

    class _FakeResponse:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=None):
        time.sleep(BATCH_BLOCK_SECONDS)
        calls.append((request, timeout))
        return _FakeResponse(success_payload)

    return fake_urlopen, calls


def test_upload_addon_zip_offloads_blocking_upload_from_event_loop(
    tmp_path, monkeypatch
):
    """A slow upload must not freeze the event loop.

    RED: with the current production code, ``upload_addon_zip`` runs its file
    read + ``urlopen`` synchronously on the loop thread, so the ticker is
    starved and this test fails. GREEN: after offloading the whole blocking
    region via ``asyncio.to_thread``, the ticker keeps firing.

    Also pins the request semantics end-to-end in one pass: exactly one
    urlopen call, with the exact URL, POST method, the file's raw bytes as
    body, and the zip content-type + auth headers; the client's own timeout
    is the one handed to urlopen; and the response JSON becomes ``result``
    with the ``bridge-addon-upload`` request_id.
    """
    zip_file = tmp_path / "test_addon.zip"
    zip_body = b"fake-zip-bytes"
    zip_file.write_bytes(zip_body)

    client = HttpBridgeClient("http://127.0.0.1:9", token="tok")
    expected_result = {"ok": True, "filename": "test_addon.zip"}
    fake_urlopen, calls = _make_slow_urlopen(expected_result)
    _patch_urlopen(monkeypatch, fake_urlopen)

    ticks, response = _run_ticker(client.upload_addon_zip(str(zip_file)))

    assert ticks >= MIN_TICKS_FOR_CONCURRENCY, (
        "event loop was blocked during the upload "
        f"({ticks} ticker tick(s); expected >= {MIN_TICKS_FOR_CONCURRENCY})"
    )
    assert len(calls) == 1, f"expected exactly one urlopen call, got {len(calls)}"
    request, timeout_arg = calls[0]
    assert request.full_url == (
        "http://127.0.0.1:9/addon/upload?filename=test_addon.zip"
    )
    assert request.get_method() == "POST"
    assert request.data == zip_body
    assert request.headers.get("Content-type") == "application/zip"
    assert request.headers.get("X-kodi-mcp-token") == "tok"
    assert timeout_arg == client.timeout

    # response shape: success JSON becomes result, request_id preserved
    assert response.request_id == "bridge-addon-upload"
    assert response.error is None
    assert response.error_type is None
    assert response.result == expected_result
    assert response.latency_ms is not None


def _run_plain(coro):
    """Drive a coroutine on a fresh, uninstalled loop (repo 3.13 slot
    constraint) and return its result — no ticker needed when a test only
    cares about the returned envelope."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_upload_addon_zip_file_not_found_mapping(tmp_path, monkeypatch):
    """A missing local file must map to the existing UNKNOWN_ERROR envelope
    (message 'local file not found: <path>') and must never reach urlopen."""
    from kodi_mcp_server.models.messages import ErrorType

    client = HttpBridgeClient("http://127.0.0.1:9")
    calls: list = []

    def no_urlopen(request, timeout=None):
        calls.append(request)
        raise AssertionError("urlopen must not be called for a missing file")

    _patch_urlopen(monkeypatch, no_urlopen)

    result = _run_plain(client.upload_addon_zip(str(tmp_path / "nope.zip")))

    assert calls == []
    assert result.request_id == "bridge-addon-upload"
    assert result.result is None
    assert result.error == f"local file not found: {tmp_path / 'nope.zip'}"
    assert result.error_type == ErrorType.UNKNOWN_ERROR


def test_upload_addon_zip_http_error_mapping(tmp_path, monkeypatch):
    """An HTTP error status must map through _http_code_to_error with the
    code, same as today (e.g. 401 -> AUTH_ERROR)."""
    from kodi_mcp_server.models.messages import ErrorType
    from urllib.error import HTTPError

    zip_file = tmp_path / "test_addon.zip"
    zip_file.write_bytes(b"fake-zip-bytes")

    client = HttpBridgeClient("http://127.0.0.1:9")

    def failing_urlopen(request, timeout=None):
        raise HTTPError(
            request.full_url, 401, "Unauthorized",
            {"Content-Type": "application/json"},  # type: ignore[arg-type]
            io.BytesIO(b""),
        )

    _patch_urlopen(monkeypatch, failing_urlopen)

    result = _run_plain(client.upload_addon_zip(str(zip_file)))

    assert result.request_id == "bridge-addon-upload"
    assert result.result is None
    assert result.error == "http error 401: Unauthorized"
    assert result.error_type == ErrorType.AUTH_ERROR
    assert result.error_code == 401
    assert result.latency_ms is not None


def test_upload_addon_zip_timeout_mapping(tmp_path, monkeypatch):
    """A socket.timeout during the upload must map to the existing TIMEOUT
    envelope, same as today (socket.timeout is TimeoutError on 3.10+)."""
    from kodi_mcp_server.models.messages import ErrorType

    zip_file = tmp_path / "test_addon.zip"
    zip_file.write_bytes(b"fake-zip-bytes")

    client = HttpBridgeClient("http://127.0.0.1:9")

    def timeout_urlopen(request, timeout=None):
        raise TimeoutError("timed out")  # == socket.timeout on Python 3.10+

    _patch_urlopen(monkeypatch, timeout_urlopen)

    result = _run_plain(client.upload_addon_zip(str(zip_file)))

    assert result.request_id == "bridge-addon-upload"
    assert result.result is None
    assert result.error == "request timeout"
    assert result.error_type == ErrorType.TIMEOUT
    assert result.latency_ms is not None


# ---------------------------------------------------------------------------
# repo_stage_upload — complex bridge method (custom http.client streaming)
#
# repo_stage_upload does not go through _make_request or urlopen: it builds
# its own http.client.HTTPConnection, streams the zip in 64 KiB chunks via
# conn.send(), and handles its own response/error mapping. The fake stands in
# for the module-level http.client.HTTPConnection — the outermost blocking
# primitive the method uses — so the fake's send() blocks the calling thread
# (simulating a slow bridge upload) and records everything the real request
# would carry.
# ---------------------------------------------------------------------------


def _make_slow_http_connection(response_body: bytes, status: int = 200,
                               reason: str = "OK", exc: BaseException | None = None):
    """A stand-in for ``http.client.HTTPConnection``.

    ``send()`` blocks the *calling thread* (``time.sleep``) to simulate a slow
    upload, then behaves like a connected socket. Records, per connection, the
    constructor kwargs (host, port, timeout), the request line, the headers
    in order, the exact chunk sequence, and whether ``close()`` was called.

    ``exc``: if set, raised from ``send()`` instead of returning a response.

    Returns ``(fake_class, connections)`` where each entry of ``connections``
    is a dict with keys: ``init``, ``request``, ``headers``, ``chunks``,
    ``closed``.
    """
    connections: list = []

    class _FakeResponse:
        def __init__(self, status, reason, body):
            self.status = status
            self.reason = reason
            self._body = body

        def read(self):
            return self._body

    class FakeHTTPConnection:
        def __init__(self, host, port, timeout=None, **kwargs):
            self._record = {
                "init": {"host": host, "port": port, "timeout": timeout, "kwargs": kwargs},
                "request": None,
                "headers": [],
                "chunks": [],
                "closed": False,
            }
            connections.append(self._record)

        def putrequest(self, method, url, *args, **kwargs):
            self._record["request"] = (method, url)

        def putheader(self, header, value):
            self._record["headers"].append((header, value))

        def endheaders(self):
            pass

        def send(self, data):
            time.sleep(BATCH_BLOCK_SECONDS)
            if exc is not None:
                raise exc
            self._record["chunks"].append(data)

        def getresponse(self):
            return _FakeResponse(status, reason, response_body)

        def close(self):
            self._record["closed"] = True

    return FakeHTTPConnection, connections


def _patch_http_connection(monkeypatch, fake_class):
    """Patch the module-level ``http.client.HTTPConnection`` that
    repo_stage_upload resolves at call time; restored by monkeypatch."""
    import kodi_mcp_server.transport.http_bridge as hb_module

    monkeypatch.setattr(hb_module.http.client, "HTTPConnection", fake_class)


def test_repo_stage_upload_offloads_blocking_stream_from_event_loop(tmp_path, monkeypatch):
    """A slow streamed upload must not freeze the event loop.

    RED: with the current production code, the ``http.client`` streaming
    region runs synchronously on the loop thread, so the ticker is starved
    and this test fails. GREEN: after offloading the whole blocking unit via
    ``asyncio.to_thread``, the ticker keeps firing.

    Also pins the request/body semantics end-to-end in one pass: exactly one
    connection, the exact request path (POST /repo/stage with repo_id and
    mode query), the exact headers (Content-Type application/zip,
    Content-Length, auth token, optional X-Repo-Version / X-Content-SHA256),
    the exact 64 KiB chunk sequence reassembling to the file bytes, the
    client's own timeout on the connection, the success envelope shape, and
    connection close in the finally path.
    """
    zip_file = tmp_path / "dev_repo.zip"
    zip_body = b"z" * (64 * 1024 + 1234)  # > 1 chunk to pin chunking
    zip_file.write_bytes(zip_body)

    envelope = {"transport": {"ok": True}, "result": {"staged": True}}
    fake_conn, connections = _make_slow_http_connection(
        json.dumps(envelope).encode("utf-8")
    )
    _patch_http_connection(monkeypatch, fake_conn)

    client = HttpBridgeClient("http://127.0.0.1:9", token="tok")
    ticks, response = _run_ticker(
        client.repo_stage_upload(
            repo_id="dev-repo",
            zip_path=str(zip_file),
            mode="overwrite",
            repo_version="1.2.3",
            sha256="abc123",
        )
    )

    assert ticks >= MIN_TICKS_FOR_CONCURRENCY, (
        "event loop was blocked during the repo stage upload "
        f"({ticks} ticker tick(s); expected >= {MIN_TICKS_FOR_CONCURRENCY})"
    )
    assert len(connections) == 1, f"expected exactly one connection, got {len(connections)}"
    record = connections[0]

    # connection constructed with the client's host/port/timeout
    assert record["init"] == {"host": "127.0.0.1", "port": 9, "timeout": client.timeout, "kwargs": {}}

    # request line: POST with repo_id + mode query
    method, url = record["request"]
    assert method == "POST"
    assert url == "/repo/stage?repo_id=dev-repo&mode=overwrite"

    # headers in the exact order the method sets them
    headers = dict(record["headers"])
    assert headers == {
        "Content-Type": "application/zip",
        "Content-Length": str(len(zip_body)),
        "X-Kodi-MCP-Token": "tok",
        "X-Repo-Version": "1.2.3",
        "X-Content-SHA256": "abc123",
    }
    assert [h for h, _ in record["headers"]] == [
        "Content-Type", "Content-Length", "X-Kodi-MCP-Token",
        "X-Repo-Version", "X-Content-SHA256",
    ]

    # chunk sequence: 64 KiB chunks, reassembling to the exact file bytes
    assert [len(c) for c in record["chunks"]] == [64 * 1024, 1234]
    assert b"".join(record["chunks"]) == zip_body

    # success response shape
    assert response.request_id == "bridge-repo-stage"
    assert response.error is None
    assert response.error_type is None
    assert response.result == envelope
    assert response.latency_ms is not None

    # connection cleanup
    assert record["closed"] is True


def test_repo_stage_upload_file_not_found_mapping(tmp_path, monkeypatch):
    """A missing local zip must map to the existing UNKNOWN_ERROR envelope
    ('local file not found: <path>') and never open a connection."""
    from kodi_mcp_server.models.messages import ErrorType

    client = HttpBridgeClient("http://127.0.0.1:9")
    fake_conn, connections = _make_slow_http_connection(b"{}")
    _patch_http_connection(monkeypatch, fake_conn)

    result = _run_plain(client.repo_stage_upload(
        repo_id="dev-repo", zip_path=str(tmp_path / "nope.zip")))

    assert connections == []
    assert result.request_id == "bridge-repo-stage"
    assert result.result is None
    assert result.error == f"local file not found: {tmp_path / 'nope.zip'}"
    assert result.error_type == ErrorType.UNKNOWN_ERROR


def test_repo_stage_upload_http_error_mapping(tmp_path, monkeypatch):
    """An HTTP error status must map through _http_code_to_error with the
    code and the parsed JSON body preserved as result (e.g. 401 ->
    AUTH_ERROR), same as today."""
    from kodi_mcp_server.models.messages import ErrorType

    zip_file = tmp_path / "dev_repo.zip"
    zip_file.write_bytes(b"z" * 100)

    body = json.dumps({"error": "invalid token"}).encode("utf-8")
    client = HttpBridgeClient("http://127.0.0.1:9")
    fake_conn, connections = _make_slow_http_connection(body, status=401, reason="Unauthorized")
    _patch_http_connection(monkeypatch, fake_conn)

    result = _run_plain(client.repo_stage_upload(
        repo_id="dev-repo", zip_path=str(zip_file)))

    assert len(connections) == 1
    assert connections[0]["closed"] is True
    assert result.request_id == "bridge-repo-stage"
    assert result.result == {"error": "invalid token"}
    assert result.error == "http error 401: Unauthorized"
    assert result.error_type == ErrorType.AUTH_ERROR
    assert result.error_code == 401
    assert result.latency_ms is not None


def test_repo_stage_upload_timeout_mapping(tmp_path, monkeypatch):
    """A socket.timeout during the streamed send must map to the existing
    TIMEOUT envelope, and the connection must still be closed (socket.timeout
    is TimeoutError on 3.10+)."""
    from kodi_mcp_server.models.messages import ErrorType

    zip_file = tmp_path / "dev_repo.zip"
    zip_file.write_bytes(b"z" * 100)

    client = HttpBridgeClient("http://127.0.0.1:9")
    fake_conn, connections = _make_slow_http_connection(b"{}", exc=TimeoutError("timed out"))
    _patch_http_connection(monkeypatch, fake_conn)

    result = _run_plain(client.repo_stage_upload(
        repo_id="dev-repo", zip_path=str(zip_file)))

    assert len(connections) == 1
    assert connections[0]["closed"] is True
    assert result.request_id == "bridge-repo-stage"
    assert result.result is None
    assert result.error == "request timeout"
    assert result.error_type == ErrorType.TIMEOUT
    assert result.latency_ms is not None


def test_repo_stage_upload_generic_error_mapping(tmp_path, monkeypatch):
    """Any other exception during the stream must map to the existing
    UNKNOWN_ERROR 'request failed' envelope, and the connection must still
    be closed."""
    from kodi_mcp_server.models.messages import ErrorType

    zip_file = tmp_path / "dev_repo.zip"
    zip_file.write_bytes(b"z" * 100)

    client = HttpBridgeClient("http://127.0.0.1:9")
    fake_conn, connections = _make_slow_http_connection(
        b"{}", exc=OSError("connection reset"))
    _patch_http_connection(monkeypatch, fake_conn)

    result = _run_plain(client.repo_stage_upload(
        repo_id="dev-repo", zip_path=str(zip_file)))

    assert len(connections) == 1
    assert connections[0]["closed"] is True
    assert result.request_id == "bridge-repo-stage"
    assert result.result is None
    assert result.error == "request failed: connection reset"
    assert result.error_type == ErrorType.UNKNOWN_ERROR
    assert result.latency_ms is not None


def test_repo_stage_upload_no_retry_on_network_failure(tmp_path, monkeypatch):
    """repo_stage_upload must make exactly one connection attempt even when
    the connect itself fails (URLError) — no retry loop may be introduced.
    URLError maps to the existing NETWORK_ERROR envelope."""
    import socket as socket_mod
    from kodi_mcp_server.models.messages import ErrorType
    from urllib.error import URLError

    zip_file = tmp_path / "dev_repo.zip"
    zip_file.write_bytes(b"z" * 100)

    attempts: list = []

    class FailingConn:
        def __init__(self, host, port, timeout=None, **kwargs):
            attempts.append((host, port, timeout))
            # the real HTTPConnection.__init__ does not open the socket, but a
            # refused connection surfaces as URLError on first use — model the
            # failure on putrequest so it is caught by the method's handler
        def putrequest(self, *a, **k):
            raise URLError(socket_mod.gaierror("name resolution failed"))
        def putheader(self, *a, **k):
            pass
        def endheaders(self):
            pass
        def send(self, *a, **k):
            raise AssertionError("must not send after failed request line")
        def getresponse(self):
            raise AssertionError("must not get a response after failure")
        def close(self):
            pass

    client = HttpBridgeClient("http://127.0.0.1:9")
    import kodi_mcp_server.transport.http_bridge as hb_module
    monkeypatch.setattr(hb_module.http.client, "HTTPConnection", FailingConn)

    result = _run_plain(client.repo_stage_upload(
        repo_id="dev-repo", zip_path=str(zip_file)))

    assert len(attempts) == 1, f"expected exactly one connection attempt, got {len(attempts)}"
    assert result.request_id == "bridge-repo-stage"
    assert result.result is None
    assert result.error == "connection error: name resolution failed"
    assert result.error_type == ErrorType.NETWORK_ERROR

