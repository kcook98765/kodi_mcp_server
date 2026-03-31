#!/usr/bin/env python3
"""Minimal smoke test for the current Kodi bridge flow."""

import json
import sys
import time
from urllib import request
from urllib.error import HTTPError, URLError

BASE_URL = "http://127.0.0.1:8000"
TAIL_LINES = 200


def fetch_json(method: str, url: str, body: bytes | None = None) -> dict:
    req = request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main() -> int:
    try:
        status = fetch_json("GET", f"{BASE_URL}/tools/get_bridge_status")
        print("bridge status ok")
    except HTTPError as exc:
        return fail(f"status HTTP {exc.code}: {exc.reason}")
    except URLError as exc:
        return fail(f"status connection error: {exc.reason}")

    addon_version = (status.get("result") or {}).get("addon_version")
    if not addon_version:
        return fail(f"missing addon_version in status response: {status}")
    print(f"addon_version={addon_version}")

    try:
        ping = fetch_json("POST", f"{BASE_URL}/tools/bridge_debug_ping", b"")
        print("debug ping ok")
    except HTTPError as exc:
        return fail(f"debug ping HTTP {exc.code}: {exc.reason}")
    except URLError as exc:
        return fail(f"debug ping connection error: {exc.reason}")

    if ping.get("error"):
        return fail(f"debug ping returned error: {ping['error']}")

    ping_result = ping.get("result") or {}
    if ping_result.get("addon_version") != addon_version:
        return fail(
            f"debug ping addon_version mismatch: status={addon_version} ping={ping_result.get('addon_version')}"
        )

    ping_timestamp = ping_result.get("timestamp")
    if ping_timestamp is None:
        return fail(f"debug ping missing timestamp: {ping}")
    print(f"debug_ping_timestamp={ping_timestamp}")

    time.sleep(1)

    try:
        tail = fetch_json("GET", f"{BASE_URL}/tools/get_bridge_log_tail?lines={TAIL_LINES}")
        print("log tail ok")
    except HTTPError as exc:
        return fail(f"log tail HTTP {exc.code}: {exc.reason}")
    except URLError as exc:
        return fail(f"log tail connection error: {exc.reason}")

    if tail.get("error"):
        return fail(f"log tail returned error: {tail['error']}")

    lines = (tail.get("result") or {}).get("lines") or []
    expected_fragment = (
        f"[service.kodi_mcp][DEBUG_PING] addon_id=service.kodi_mcp "
        f"addon_version={addon_version} timestamp={ping_timestamp}"
    )
    matched = [line for line in lines if expected_fragment in line]
    if not matched:
        return fail(
            "debug marker not found in returned log tail for latest ping; "
            f"expected fragment: {expected_fragment}"
        )

    print("debug marker found in log tail")
    print("SMOKE OK: bridge status, debug ping, and log tail verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
