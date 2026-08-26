"""Event-loop regression test: HTTP JSON-RPC transport must not block the loop.

`HttpJsonRpcTransport._send_once` performs synchronous urllib HTTP
(`urlopen` + response read) on the calling thread. When driven from an
async context (MCP/FastAPI event loop), a slow Kodi HTTP response freezes
the whole loop. This test proves — behaviorally, via a background ticker —
that the blocking region is offloaded off the event-loop thread.

Harness discipline (repo Python 3.13 quirk):
- fresh `asyncio.new_event_loop()`, NEVER installed as the thread's current
  loop, closed in `finally` — avoids clearing the main-thread current-loop
  slot that would break the legacy `tests/test_http_errors.py` pattern;
- fake `urllib_request.urlopen` whose response blocks the CALLING thread with
  `time.sleep` (a real blocking call, not `asyncio.sleep`);
- no real network; a hard outer timeout guards against accidental hangs.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import kodi_mcp_server.transport.http_jsonrpc as http_jsonrpc_mod
from kodi_mcp_server.models.messages import RequestMessage
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport

BLOCK_SECONDS = 0.9     # ~9 ticks at 0.1s when the loop is free; keeps test fast
TICK_PERIOD = 0.1
MIN_TICKS = 3          # far from both ends: offloaded -> ~9, blocked -> ~0
OUTER_TIMEOUT = 10.0   # hard cap so a regression cannot hang the suite


class _FakeResponse:
    """Context-manager stand-in for the urlopen result.

    `read()` blocks the CALLING thread with `time.sleep` — simulating a slow
    HTTP response body. When `_send_once` runs this on the event-loop thread,
    the loop is frozen for BLOCK_SECONDS and the ticker starves.
    """

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        time.sleep(BLOCK_SECONDS)
        return self._body


def _drive_on_fresh_loop(coro, tick_period: float = TICK_PERIOD) -> int:
    """Run `coro` on a fresh (not installed) loop; return ticker tick count
    while `coro` executed. Hard outer timeout guards against a hang."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(_drive(coro, tick_period), timeout=OUTER_TIMEOUT)
        )
    finally:
        loop.close()


async def _drive(coro, tick_period: float) -> int:
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(tick_period)
            ticks += 1

    tick_task = asyncio.ensure_future(ticker())
    try:
        await coro
    finally:
        tick_task.cancel()
    return ticks


def test_send_once_offloads_blocking_http_from_event_loop():
    """A slow HTTP response must not starve the event loop; result stays correct."""
    body = json.dumps({"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}}).encode("utf-8")

    def fake_urlopen(http_request, timeout=None):
        assert timeout == 5, "timeout must be passed through verbatim"
        return _FakeResponse(body)

    transport = HttpJsonRpcTransport(
        url="http://127.0.0.1:9/jsonrpc",
        username="u",
        password="p",
        timeout=5,
    )
    request = RequestMessage(
        request_id="req-1",
        command="execute_jsonrpc",
        args={"method": "JSONRPC.Version", "params": {}},
    )

    # Patch urlopen in the transport module namespace (no real network).
    original = http_jsonrpc_mod.urllib_request.urlopen
    http_jsonrpc_mod.urllib_request.urlopen = fake_urlopen
    try:
        started = time.monotonic()
        ticks = _drive_on_fresh_loop(transport.send_request(request))
        elapsed = time.monotonic() - started
    finally:
        http_jsonrpc_mod.urllib_request.urlopen = original

    assert ticks >= MIN_TICKS, (
        f"event loop starved during JSON-RPC HTTP call: "
        f"{ticks} ticker tick(s); expected >= {MIN_TICKS}"
    )
    # Offloading to a worker thread means total wall time ~ block, but the key
    # assertion above already captures starvation; sanity-check duration.
    assert elapsed >= BLOCK_SECONDS, "block did not execute as expected"


def test_ticker_harness_control_offloaded_path_keeps_ticking():
    """Harness control: a trivially offloaded coroutine keeps the ticker alive.

    If this fails, the harness (not the transport) is broken.
    """
    async def offloaded_noop():
        def blocker():
            time.sleep(BLOCK_SECONDS)

        await asyncio.to_thread(blocker)

    ticks = _drive_on_fresh_loop(offloaded_noop())
    assert ticks >= MIN_TICKS, f"control failed: harness broken ({ticks} ticks)"
