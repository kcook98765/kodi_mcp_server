"""Remote MCP (/mcp) smoke test.

Usage (from repo root):

    .\.venv\Scripts\python.exe scripts\mcp_remote_smoke.py

Set optional API key:
    set MCP_API_KEY=...
    .\.venv\Scripts\python.exe scripts\mcp_remote_smoke.py
"""

from __future__ import annotations

import os

import httpx


BASE_URL = os.getenv("MCP_REMOTE_URL", "http://127.0.0.1:8000")
MCP_URL = BASE_URL.rstrip("/") + "/mcp/"


def _headers(*, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    api_key = os.getenv("MCP_API_KEY")
    if api_key:
        headers["x-mcp-api-key"] = api_key
    if session_id:
        headers["mcp-session-id"] = session_id
        headers["mcp-protocol-version"] = "2025-11-25"
    return headers


def _print_sse_lines(response: httpx.Response, *, max_lines: int = 40) -> list[str]:
    lines: list[str] = []
    for i, line in enumerate(response.iter_lines()):
        if i >= max_lines:
            break
        if line is None:
            continue
        line_str = line.decode() if isinstance(line, (bytes, bytearray)) else str(line)
        print(line_str)
        lines.append(line_str)
    return lines


def main() -> int:
    payload_init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "mcp_remote_smoke", "version": "0"},
        },
    }

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        print(f"POST {MCP_URL} initialize")
        with client.stream("POST", MCP_URL, headers=_headers(), json=payload_init) as r:
            print("status:", r.status_code)
            print("content-type:", r.headers.get("content-type"))
            session_id = r.headers.get("mcp-session-id")
            print("mcp-session-id:", session_id)
            _print_sse_lines(r, max_lines=60)

        if not session_id:
            print("ERROR: missing mcp-session-id header")
            return 2

        payload_list = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        print(f"\nPOST {MCP_URL} tools/list")
        with client.stream(
            "POST",
            MCP_URL,
            headers=_headers(session_id=session_id),
            json=payload_list,
        ) as r:
            print("status:", r.status_code)
            print("content-type:", r.headers.get("content-type"))
            print("mcp-session-id:", r.headers.get("mcp-session-id"))
            _print_sse_lines(r, max_lines=80)

        payload_call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "kodi_status", "arguments": {}},
        }
        print(f"\nPOST {MCP_URL} tools/call kodi_status")
        with client.stream(
            "POST",
            MCP_URL,
            headers=_headers(session_id=session_id),
            json=payload_call,
        ) as r:
            print("status:", r.status_code)
            print("content-type:", r.headers.get("content-type"))
            print("mcp-session-id:", r.headers.get("mcp-session-id"))
            _print_sse_lines(r, max_lines=120)

    # Helpful for manual reproduction.
    print_curl_examples(session_id)

    return 0


def print_curl_examples(session_id: str) -> None:
    """Print exact curl.exe commands to reproduce the smoke test."""

    api_key = os.getenv("MCP_API_KEY")
    api_key_header = f" -H \"x-mcp-api-key: {api_key}\"" if api_key else ""

    init_payload = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2025-11-25","capabilities":{},'
        '"clientInfo":{"name":"curl","version":"0"}}}'
    )
    list_payload = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
    call_payload = (
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":'
        '{"name":"kodi_status","arguments":{}}}'
    )

    # For curl.exe on Windows, safest is to pass JSON with escaped quotes.
    def _curl_json_arg(raw: str) -> str:
        return raw.replace('"', r'\\"')

    init_payload_arg = _curl_json_arg(init_payload)
    list_payload_arg = _curl_json_arg(list_payload)
    call_payload_arg = _curl_json_arg(call_payload)

    print("\n--- curl.exe examples (Windows) ---")
    print(
        "curl.exe --globoff -i -N -X POST http://127.0.0.1:8000/mcp/ "
        "-H \"Content-Type: application/json\" "
        "-H \"Accept: application/json, text/event-stream\" "
        f"{api_key_header} "
        f"--data-raw \"{init_payload_arg}\""
    )
    print(
        "curl.exe --globoff -i -N -X POST http://127.0.0.1:8000/mcp/ "
        "-H \"Content-Type: application/json\" "
        "-H \"Accept: application/json, text/event-stream\" "
        f"{api_key_header} "
        f"-H \"mcp-session-id: {session_id}\" "
        "-H \"mcp-protocol-version: 2025-11-25\" "
        f"--data-raw \"{list_payload_arg}\""
    )
    print(
        "curl.exe --globoff -i -N -X POST http://127.0.0.1:8000/mcp/ "
        "-H \"Content-Type: application/json\" "
        "-H \"Accept: application/json, text/event-stream\" "
        f"{api_key_header} "
        f"-H \"mcp-session-id: {session_id}\" "
        "-H \"mcp-protocol-version: 2025-11-25\" "
        f"--data-raw \"{call_payload_arg}\""
    )


if __name__ == "__main__":
    rc = main()
    # We intentionally print curl commands using the last session id only
    # if the smoke test succeeded.
    # (The script already printed the session id header during initialize.)
    raise SystemExit(rc)
