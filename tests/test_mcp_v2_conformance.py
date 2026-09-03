"""MCP 2.x conformance and regression coverage.

These tests exercise the real v2 dispatch path (initialize negotiation,
capability advertisement, tools/list + tools/call over the in-memory
transport) rather than the removed ``Server.request_handlers`` dict, and
they pin the behavior that the v1 -> v2 migration must preserve:

- server identity (name/version/instructions) in initialize results
- legacy (handshake-era) protocol compatibility: a 2025-11-25 client
  still gets the initialize handshake and Mcp-Session-Id semantics
- modern (2026-07-28) protocol path: no initialize; first request opens
  the connection
- tool list is complete and schemas are unchanged
- tool error semantics: model-visible ``is_error`` results, not
  protocol-level JSON-RPC errors
- unknown tools produce a model-visible error result
- no production code path depends on the removed ``request_handlers``
"""

import json

import pytest
from jsonschema import Draft202012Validator

from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime
from kodi_mcp_mcp.tool_contract import EXPECTED_TOOL_NAMES
from kodi_mcp_server import __version__
from mcp.client import Client
from mcp.shared.memory import create_client_server_memory_streams


def _server():
    runtime = build_runtime()
    server, init_options = build_mcp_server(runtime)
    return server, init_options


@pytest.mark.asyncio
async def test_v2_no_removed_request_handlers_attribute():
    """The v1 handler dict must not be relied on or emulated."""
    server, _ = _server()
    assert not hasattr(server, "request_handlers"), (
        "v2 migration regression: Server.request_handlers reappeared"
    )
    # The supported public lookup works instead:
    assert server.get_request_handler("tools/call") is not None
    assert server.get_request_handler("tools/list") is not None
    assert server.get_request_handler("initialize") is None
    assert server.get_request_handler("no/such/method") is None


@pytest.mark.asyncio
async def test_v2_initialize_negotiates_supported_handshake_version():
    """A legacy (handshake-era) client works end-to-end: entering the
    client performs the initialize handshake, and server
    identity/instructions/capabilities are derived from the v2 Server
    construction (no user initialize handler)."""
    server, init_options = _server()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with Client(server, mode="legacy") as client:
            # The legacy era requires the initialize handshake to have
            # completed before any tool request; list_tools proves both.
            result = await client.list_tools()
            assert "kodi_status" in {t.name for t in result.tools}
            # Server identity comes from the constructor in v2 and feeds
            # the runner-built InitializeResult.
            assert init_options.server_name == "kodi-mcp"
            assert init_options.server_version == __version__
            assert init_options.server_version != "0.0.0"
            assert "Kodi MCP server" in init_options.instructions
            # Tools capability is advertised (derived from registered handlers).
            assert init_options.capabilities.tools is not None


@pytest.mark.asyncio
async def test_v2_tools_list_complete_and_schemas_stable():
    """Full tool surface is listed over the real dispatch path and the
    kodi_status schema matches the v1 shape."""
    server, _ = _server()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with Client(server, mode="auto") as client:
            result = await client.list_tools()
            names = {t.name for t in result.tools}
            assert names == EXPECTED_TOOL_NAMES
            by_name = {t.name: t for t in result.tools}
            for tool_name, tool in by_name.items():
                Draft202012Validator.check_schema(tool.input_schema)
                assert tool.input_schema["type"] == "object", tool_name
                assert tool.input_schema["additionalProperties"] is False, tool_name
            status = by_name["kodi_status"]
            assert status.description.startswith("Get end-to-end server status")


@pytest.mark.asyncio
async def test_v2_representative_tool_call_succeeds():
    """kodi_status runs end-to-end over the real dispatch path."""
    server, _ = _server()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with Client(server, mode="auto") as client:
            result = await client.call_tool("kodi_status", {})
            assert result.is_error is False
            envelope = json.loads(result.content[0].text)
            assert envelope["ok"] is True
            assert envelope["tool"] == "kodi_status"
            assert envelope["data"]["server"]["status"] == "running"


@pytest.mark.asyncio
async def test_v2_tool_error_semantics_are_model_visible():
    """Invalid tool parameters produce a model-visible is_error result
    (not an opaque protocol-level JSON-RPC error) and keep the stable
    envelope shape with error_type."""
    server, _ = _server()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with Client(server, mode="auto") as client:
            result = await client.call_tool("kodi_player_seek", {"playerid": 1})
            assert result.is_error is True
            envelope = json.loads(result.content[0].text)
            assert envelope["ok"] is False
            assert envelope["tool"] == "kodi_player_seek"
            assert envelope["error_type"] == "invalid_params"
            assert "seconds" in envelope["error"]

            # Missing required arg for addon_execute keeps the alias-aware
            # missing-arg behavior (model-visible, not a protocol error).
            result2 = await client.call_tool("addon_execute", {"wait": False})
            assert result2.is_error is True
            env2 = json.loads(result2.content[0].text)
            assert env2["ok"] is False
            assert env2["error_type"] == "invalid_params"
            assert "addonid" in env2["error"]


@pytest.mark.asyncio
async def test_v2_unknown_tool_returns_model_visible_error():
    """Calling a tool outside the whitelist is a model-visible error result
    (the legacy 'Tool not implemented' fallback), not a raised
    JSON-RPC protocol error."""
    server, _ = _server()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with Client(server, mode="auto") as client:
            result = await client.call_tool("definitely_not_a_real_tool", {})
            assert result.is_error is True
            text = result.content[0].text
            assert "Tool not implemented" in text
            assert "definitely_not_a_real_tool" in text


@pytest.mark.asyncio
async def test_v2_legacy_handshake_era_still_served():
    """Older-protocol (handshake-era) clients remain supported: the
    dual-era loop serves 2025-11-25 clients with the initialize
    handshake while the modern era skips it."""
    server, _ = _server()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with Client(server, mode="legacy") as client:
            # legacy mode performs initialize first; if the handshake-era
            # path were broken this would raise.
            result = await client.list_tools()
            names = {t.name for t in result.tools}
            assert "kodi_status" in names


def test_v2_streamable_http_initialize_and_tool_listing():
    """The remote StreamableHTTP transport (the real
    ``StreamableHTTPSessionManager`` + ``handle_request`` path mounted at
    ``/mcp``) initializes a legacy-era session and lists tools end-to-end.

    This exercises the HTTP/server path that the unit tests cannot reach
    (session-manager lifespan + ASGI request routing + Mcp-Session-Id).
    Responses are SSE-framed (``json_response=False``), so the JSON-RPC
    payload lives on the ``data:`` line of each ``message`` event.
    """
    import json

    from starlette.testclient import TestClient
    from fastapi import FastAPI

    from kodi_mcp_server.remote_mcp_app import create_remote_mcp

    remote_asgi_app, remote_lifespan = create_remote_mcp()

    async def lifespan(_: FastAPI):
        async with remote_lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", remote_asgi_app)

    def sse_payload(response):
        """Extract the JSON-RPC message from an SSE ``event: message`` frame."""
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("text/event-stream")
        data_lines = [
            line[len("data:"):].strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        assert data_lines, f"expected SSE data frame, got: {response.text!r}"
        return json.loads(data_lines[0])

    with TestClient(app) as tc:
        init = tc.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "conformance", "version": "0"},
                },
            },
        )
        session_id = init.headers.get("mcp-session-id")
        assert session_id, "legacy-era session must issue a Mcp-Session-Id"
        init_body = sse_payload(init)
        assert init_body["id"] == 1
        assert init_body["result"]["serverInfo"]["name"] == "kodi-mcp"
        assert init_body["result"]["protocolVersion"] == "2025-11-25"
        assert "tools" in init_body["result"]["capabilities"]

        tools = tc.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"mcp-session-id": session_id, "mcp-protocol-version": "2025-11-25"},
        )
        tools_body = sse_payload(tools)
        assert tools_body["id"] == 2
        tool_names = {t["name"] for t in tools_body["result"]["tools"]}
        assert "kodi_status" in tool_names

        invalid_calls = [
            (3, "managed_addon_validate_state", {}, "managed_addon_id"),
            (4, "kodi_gui_screenshot", {"store": "yes"}, "store"),
            (
                5,
                "managed_addon_validate_state",
                {"managed_addon_id": "Plugin/Bad"},
                "managed_addon_id",
            ),
        ]
        for request_id, tool_name, arguments, field in invalid_calls:
            response = tc.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                headers={
                    "mcp-session-id": session_id,
                    "mcp-protocol-version": "2025-11-25",
                },
            )
            body = sse_payload(response)
            assert body["id"] == request_id
            assert "error" not in body
            assert body["result"]["isError"] is True
            envelope = json.loads(body["result"]["content"][0]["text"])
            assert envelope["ok"] is False
            assert envelope["error_type"] == "invalid_params"
            assert field in envelope["error"]


@pytest.mark.asyncio
async def test_mcp_dispatch_gate_invariant():
    """Pins the invariant that every name in the dispatch gate set has an
    explicit branch in the if/elif chain and that the terminal else raises
    instead of calling _kodi_status.

    This prevents silent fall-through: if a future developer adds a name
    to the 36-name gate literal without adding a corresponding elif branch,
    the terminal else will raise NotImplementedError instead of silently
    calling _kodi_status (which would produce the wrong envelope).
    """
    import re
    from pathlib import Path

    # Read the server_core.py source
    repo_root = Path(__file__).resolve().parents[1]
    server_core_path = repo_root / "src" / "kodi_mcp_mcp" / "server_core.py"
    source = server_core_path.read_text()

    # Extract gate names: the set literal after `if tool_name in {`
    gate_pattern = r'if tool_name in \{([^}]+)\}'
    gate_match = re.search(gate_pattern, source)
    assert gate_match, "dispatch gate set literal not found"
    gate_text = gate_match.group(1)
    gate_names = {
        name.strip().strip('"').strip("'")
        for name in gate_text.split(",")
        if name.strip()
    }

    # Extract branch names from the dispatch chain beginning immediately after
    # the handler's start-time marker. Avoid pinning source line numbers: this
    # contract test must survive unrelated helper growth above the dispatcher.
    lines = source.splitlines()
    branch_names = set()
    in_dispatch_chain = False
    terminal_else_found = False
    saw_start_marker = False
    for i, line in enumerate(lines):
        if line.strip() == "start = time.time()":
            saw_start_marker = True
            continue
        if saw_start_marker and line.strip() == "try:":
            in_dispatch_chain = True
            continue
        if (
            in_dispatch_chain
            and line.strip().startswith("else:")
            and i + 1 < len(lines)
            and "no dispatch implementation for whitelisted tool" in lines[i + 1]
        ):
            terminal_else_found = True
            next_line = lines[i + 1]
            assert next_line.strip().startswith("raise "), (
                "terminal else must raise NotImplementedError, not call _kodi_status"
            )
            assert "_kodi_status(" not in next_line, (
                "terminal else must not call _kodi_status"
            )
            break
        if in_dispatch_chain:
            # Match `elif tool_name == "X":` or `if tool_name == "X":`
            eq_match = re.search(r' tool_name == "([^"]+)"', line)
            if eq_match:
                branch_names.add(eq_match.group(1))
            # Match `elif tool_name in {"A", "B", ...}` or single name
            in_match = re.search(r' tool_name in \{([^}]+)\}', line)
            if in_match:
                branch_names.update(
                    name.strip().strip('"').strip("'")
                    for name in in_match.group(1).split(",")
                    if name.strip()
                )

    assert terminal_else_found, "terminal else block not found in expected location"

    # Assert gate and branch name sets are equal
    assert gate_names == branch_names, (
        f"dispatch gate and branch name sets must match:\n"
        f"  gate ({len(gate_names)} names): {sorted(gate_names)}\n"
        f"  branches ({len(branch_names)} names): {sorted(branch_names)}\n"
        f"  missing from branches: {gate_names - branch_names}\n"
        f"  extra in branches: {branch_names - gate_names}"
    )
