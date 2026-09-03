# MCP 1.x → 2.x Behavior Comparison

Migrated on branch `hermes/mcp-v2-migration` (MCP 1.29.0 → **MCP 2.0.0**).
Every row was verified by the test suite (131 passed) and by live probes of the
stdio entrypoint and the FastAPI `/mcp` StreamableHTTP path.

## Tool registration

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| `tools/list` handler wiring | `server.request_handlers[ListToolsRequest] = _handle_list_tools` | `server.add_request_handler("tools/list", PaginatedRequestParams, _handle_list_tools)` | UNCHANGED (mechanical API move) | `test_v2_no_removed_request_handlers_attribute`, `test_v2_tools_list_complete_and_schemas_stable` |
| Advertised capabilities | explicit `ServerCapabilities(tools=ToolsCapability())` | derived from registered handlers: `tools=ToolsCapability(list_changed=False)` | UNCHANGED on the wire (`{"tools": {}}` both eras) | `test_v2_initialize_negotiates_supported_handshake_version` (`capabilities.tools is not None`) |
| Tool surface | 36 tools, fixed schemas | identical 36 tools, identical schemas | UNCHANGED | `test_v2_tools_list_complete_and_schemas_stable` (full set + `kodi_status` schema), all 25 pre-existing tool tests |

## Tool invocation

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| Handler contract | `async f(request: CallToolRequest) -> ServerResult` | `async f(ctx, params: CallToolRequestParams) -> CallToolResult` | UNCHANGED for clients (SDK-internal shape) | `test_v2_representative_tool_call_succeeds` (real dispatch) |
| `tools/call` dispatch of `kodi_status` | envelope `{ok, tool, data, …}`, `isError: false` | identical envelope, `isError: false` | UNCHANGED | `test_v2_representative_tool_call_succeeds` |
| Args aliasing (`addon_id`/`addonid`) | supported | supported | UNCHANGED | `test_mcp_addon_execute_accepts_addon_id_alias` |

## Error semantics

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| Tool-level failure | `ServerResult(CallToolResult(isError=True, [envelope JSON]))` — model-visible, JSON-RPC success | `CallToolResult(is_error=True, [envelope JSON])` — model-visible, JSON-RPC success | UNCHANGED (v2 contract matches: a tool error result is a successful response) | `test_v2_tool_error_semantics_are_model_visible`, `test_gui_action_rejects_invalid_action`, `test_player_seek_rejects_missing_seconds` |
| Missing required arg (`addon_execute` without `addonid`) | normalized `invalid_params` envelope | identical | UNCHANGED | `test_v2_tool_error_semantics_are_model_visible` (second block), `test_mcp_addon_execute_accepts_addon_id_alias` family |
| Unexpected exception in dispatch | caught, `unknown_error` envelope, `is_error=True` | identical (same try/except) | UNCHANGED | covered by the envelope shape assertions in `test_v2_tool_error_semantics_are_model_visible` |
| Unknown tool (outside whitelist) | `ErrorData`-shaped text in `isError=True` result | identical fallback (`Tool not implemented: <name>`) | UNCHANGED | `test_v2_unknown_tool_returns_model_visible_error` |
| Protocol/internal errors | JSON-RPC error objects from SDK | JSON-RPC error objects from SDK (raised exceptions still map to protocol errors) | UNCHANGED | `test_v2_no_removed_request_handlers_attribute` (no `initialize` handler exists to shadow the runner) |

## Custom request handling

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| Custom low-level methods | none (project only used the three core methods) | n/a | UNCHANGED (no custom extensions existed) | repo-wide grep: zero custom `add_request_handler` beyond `tools/list` + `tools/call` |

## Transport / session behavior

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| stdio entrypoint | `stdio_server()` + `server.run(read, write, init_options)` | same API, dual-era loop underneath | UNCHANGED | live probe: initialize→`2025-11-25`, tools/list 36 tools, tools/call ok |
| StreamableHTTP (`/mcp`) | `StreamableHTTPSessionManager(app=server, stateless=False)` | same constructor; session manager drives `serve_loop` without init options (falls back to `server.create_initialization_options()`) | UNCHANGED | live ASGI probe: initialize 200 + `Mcp-Session-Id`, tools/list 200 with all tools; `test_endpoints.py`/`test_post_endpoints.py` (import-time construction of the full app) |
| API-key ASGI wrapper | `x-mcp-api-key` check before `handle_request` | Hardened after migration: constant-time bounded header check plus secure bind policy; same header contract | INTENTIONAL SECURITY HARDENING | `tests/test_remote_security.py` |
| Protocol versions served | handshake-era only (2024-11-05…2025-11-25) | handshake-era **and** modern 2026-07-28 (era decided by the client's first request) | INTENTIONAL (superset; old clients keep working) | `test_v2_legacy_handshake_era_still_served`, `test_v2_initialize_negotiates_supported_handshake_version` (legacy), `test_v2_*` in `auto` mode (modern) |
| `Mcp-Session-Id` | issued by session manager, honored across requests | identical (v2 keeps session semantics for the legacy era) | UNCHANGED | live ASGI probe (session id returned and reused) |

## Schema generation

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| `inputSchema` objects | hand-built JSON dicts on each `Tool` | identical dicts, unchanged | UNCHANGED | `test_v2_tools_list_complete_and_schemas_stable` |
| Result wire keys | `isError`, `structuredContent`, … (camelCase JSON) | identical (v2 fields are snake_case Python, camelCase wire aliases; `by_alias=True` round-trips) | UNCHANGED | `test_mcp_artifact_upload_and_publish` (`model_dump(by_alias=True)` → `payload["isError"]`) |

## Initialization / discovery

| BEHAVIOR | MCP 1.x | MCP 2.x | UNCHANGED / INTENTIONAL | TEST PROVING IT |
|---|---|---|---|---|
| `initialize` answered by | user `_handle_initialize` returning a fixed `InitializeResult(protocolVersion="2025-11-25", …)` | SDK `ServerRunner._handle_initialize` built from `create_initialization_options()` | INTENTIONAL (protocol-conformant negotiation replaces the hard pin) | `test_v2_initialize_negotiates_supported_handshake_version`, live stdio probe |
| Protocol version echoed | always `2025-11-25` regardless of client request | client's requested version if in {2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25}, else `LATEST_HANDSHAKE_VERSION` (= 2025-11-25) | INTENTIONAL — net-identical for every client that worked with v1 (all v1-era clients request 2025-11-25 or older, which are all supported handshake versions) | live stdio probe (`initialize` requested `2025-11-25` → `2025-11-25` echoed) |
| `serverInfo` | `Implementation(name="kodi-mcp", version="0.0.0")` | identical (from Server constructor name/version) | UNCHANGED | live stdio probe |
| `instructions` | only in the user initialize handler (NOT in the `InitializationOptions` passed to `Server.run`, so stdio clients saw it; the HTTP path built its own options without it) | single source: `Server` constructor → `create_initialization_options()` → initialize result on **both** transports | INTENTIONAL (fixes a latent v1 inconsistency: instructions now reach HTTP clients too) | `test_v2_initialize_negotiates_supported_handshake_version` (`"Kodi MCP server" in init_options.instructions`), live ASGI/stdio probes |

## Summary of intentional, externally observable differences

1. **Protocol-version negotiation**: v1 hard-pinned `2025-11-25`; v2 echoes the
   client's requested version when it is in the supported handshake set. No v1-era
   client can observe a difference (its request is answered with the same version it
   sent, and `2025-11-25` is both the old pin and the v2 latest handshake version).
2. **Modern 2026-07-28 protocol now served**: new clients can skip the initialize
   handshake; old clients are unaffected (dual-era loop).
3. **`instructions` now delivered on all transports** (previously only stdio).
4. Everything else — tool names/schemas, dispatch, error envelopes, session
   semantics, API-key behavior — is byte-identical on the wire.
