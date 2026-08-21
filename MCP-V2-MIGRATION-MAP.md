# MCP v1 → v2 Migration Map

Scope: `kodi_mcp_server` on branch `hermes/mcp-v2-migration`, migrating from MCP Python SDK
1.x (last installed 1.29.0, pinned `mcp>=1.27.0,<2.0.0`) to MCP 2.x (installed: **2.0.0**).

All v2 API facts below were verified against the installed 2.0.0 source in
`.venv/lib/python3.13/site-packages/mcp/`, not against documentation summaries.

## v2 API facts established (verified against installed source)

| Fact | Evidence |
|---|---|
| `Server.__init__` takes `on_*` callbacks; **no `capabilities=` kwarg** | `inspect.signature(Server.__init__)` raises `TypeError` on `capabilities=` |
| Handler signature is `(ctx: ServerRequestContext, params: <ParamsModel>) -> <ResultModel>` (unwrapped result) | `Server.__init__` annotations; `HandlerEntry.handler: RequestHandler` |
| `initialize` is handled by the SDK runner (`ServerRunner._handle_initialize`), built from `create_initialization_options()`; user handler override is not supported | `mcp/server/runner.py` `_handle_initialize`, `_negotiate_initialize` |
| `capabilities` are **derived** from registered handlers via `get_capabilities()` (`tools: ToolsCapability(list_changed=False)` when `tools/call`/`tools/list` registered) | `Server.get_capabilities` source |
| `add_request_handler(method: str, params_type, handler)` is the supported registration API | `Server.add_request_handler` |
| `get_request_handler(method: str) -> HandlerEntry | None` (public, docstring'd) exposes the registered entry; `.handler` is the callable | `Server.get_request_handler` |
| `Server.run(read, write, initialization_options)` still exists (dual-era loop wrapper) | `Server.run` source |
| `StreamableHTTPSessionManager(app, event_store, json_response, stateless, security_settings, retry_interval, session_idle_timeout, max_request_body_size)` — signature compatible with current call site; stateful path calls `serve_loop` without init options → falls back to `server.create_initialization_options()` | `StreamableHTTPSessionManager.__init__` sig + `streamable_http_manager.py:324` |
| `mcp.types.CallToolRequest` / `ListToolsRequest` / `InitializeRequest` (request wrappers with `method`+`params`) still exist but are no longer dispatch keys; `mcp.types.CallToolRequestParams`, `PaginatedRequestParams` are the handler param types | `mcp.types` model fields |
| v2 models use snake_case attributes; camelCase construction is accepted (`populate_by_name=True`) but **attribute reads must be snake_case** (`result.is_error`, not `result.isError`) | `CallToolResult` field `is_error`; `hasattr(r,'isError') is False` |
| `ServerResult` is now a bare type alias (`InitializeResult | ...`), not a wrapper class; handlers return the result model directly | `mcp.types.ServerResult` is a `typing.Union` |
| `LATEST_HANDSHAKE_VERSION == "2025-11-25"`; handshake era versions = {2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25}; modern era adds 2026-07-28 (no handshake; first request decides era) | `mcp_types.version` |
| In-memory test transports: `mcp.client.Client(server, mode=...)` + `create_client_server_memory_streams()` (`mcp.client._memory`) — verified working end-to-end in both `legacy` and `auto` modes | scratch prototype `/workspaces/proto_v2.py` |

## Usage inventory (whole-repo grep, 2026-08-20)

| File | v1 API | Sites | Classification |
|---|---|---|---|
| `src/kodi_mcp_mcp/server_core.py` | `Server.request_handlers[...]` registration | 3 | MECHANICAL |
| `src/kodi_mcp_mcp/server_core.py` | `_handle_initialize` override returning `ServerResult(InitializeResult(...))` | 1 | SEMANTIC (initialize moved to SDK runner) |
| `src/kodi_mcp_mcp/server_core.py` | `ServerResult(...)` result wrapping | 25 | MECHANICAL |
| `src/kodi_mcp_mcp/server_core.py` | handler signatures `(request: XxxRequest) -> ServerResult` reading `request.params` | 3 + 35 | MECHANICAL |
| `src/kodi_mcp_mcp/server_core.py` | explicit `InitializationOptions(server_name, server_version, capabilities=...)` | 1 | SEMANTIC (now derived from server) |
| `src/kodi_mcp_mcp/server.py` | `stdio_server()` + `server.run(read, write, init_options)` | 1 | UNCHANGED (v2-compatible) |
| `src/kodi_mcp_server/remote_mcp_app.py` | `StreamableHTTPSessionManager(app=server, ...)` | 1 | UNCHANGED (v2-compatible) |
| `tests/test_mcp_artifact_tools.py` | `server.request_handlers[CallToolRequest](CallToolRequest(method=..., params=...))`, `.root`, `.root.isError` | 17 calls / 18 `.root` reads | TEST-ONLY DEPENDENCY ON INTERNAL API |
| `tests/test_mcp_gui_tools.py` | same pattern | 6 calls / 7 `.root` reads | TEST-ONLY DEPENDENCY ON INTERNAL API |
| `tests/test_mcp_player_tools.py` | same pattern incl. `request_handlers[ListToolsRequest]` | 8 calls / 6 `.root` reads | TEST-ONLY DEPENDENCY ON INTERNAL API |
| `scripts/mcp_remote_smoke.py` | raw HTTP JSON-RPC against `/mcp` with `protocolVersion: 2025-11-25` + `Mcp-Session-Id` | manual script, not in pytest | UNCHANGED (legacy-protocol path still served by v2) |

No uses found anywhere of: `FastMCP`, `MCPServer`, `request_context`, `get_context`,
`ClientSession`, SSE-only APIs, custom JSON-RPC request methods, server-initiated
requests, or `mcp.server.fastmcp.*`.

## Per-usage migration plan

### 1. Handler registration (production, `server_core.py`)
- OLD: `server.request_handlers[InitializeRequest/ListToolsRequest/CallToolRequest] = ...`
- NEW: `server.add_request_handler("tools/list", PaginatedRequestParams, _handle_list_tools)`
  and `server.add_request_handler("tools/call", CallToolRequestParams, _handle_call_tool)`.
- BEHAVIORAL CHANGE: none for clients — same methods, same schemas, same dispatch.
  Capabilities advertised become `tools: ToolsCapability(list_changed=False)`
  (serialized as `{"tools": {}}` on the wire, identical to v1's `ToolsCapability()`).
- RISK: low. Public API; verified in prototype.

### 2. `initialize` handling (production)
- OLD: user `_handle_initialize` returning `ServerResult(InitializeResult(protocolVersion="2025-11-25", capabilities=..., serverInfo=Implementation(name=SERVER_NAME, version=SERVER_VERSION), instructions="Kodi MCP server ..."))`.
- NEW: removed. SDK runner answers `initialize` from `create_initialization_options()`,
  which reads `server.name`/`server.version`/`instructions` (constructor args) and derives
  capabilities from registered handlers. `protocolVersion` is negotiated by the runner:
  a client requesting a supported handshake version gets that version echoed; a client
  requesting an unknown version gets `LATEST_HANDSHAKE_VERSION` (2025-11-25).
- BEHAVIORAL CHANGE (intentional, protocol-conformant):
  - v1 hard-pinned `protocolVersion` to 2025-11-25 regardless of client request.
    v2 echoes the client's requested version if it is in the supported handshake set
    (2024-11-05 … 2025-11-25). Net effect for all clients that work with v1: identical
    (2025-11-25 is both the v1 pin and the v2 `LATEST_HANDSHAKE_VERSION`).
  - `serverInfo` gains nothing new (same name/version); `instructions` now flows from
    the Server constructor into the initialize result (previously it was NOT in
    `init_options` used by `Server.run`, only in the override — now consistent on all
    transports).
- RISK: low. `SERVER_NAME`/`SERVER_VERSION` unchanged; instructions text unchanged.

### 3. Handler signatures + result unwrapping (production)
- OLD: `async def _handle_list_tools(_: ListToolsRequest) -> ServerResult` …
  `return ServerResult(ListToolsResult(tools=tools))`.
- NEW: `async def _handle_list_tools(ctx, params: PaginatedRequestParams | None) -> ListToolsResult`
  … `return ListToolsResult(tools=tools)`. Same for `tools/call`
  (`(ctx, params: CallToolRequestParams) -> CallToolResult`), with all 35
  `request.params.X` reads renamed to `params.X` (mechanical, 34×`arguments` + 1×`name`).
- `isError=` kwarg → `is_error=` (canonical v2 form; both accepted at construction).
- BEHAVIORAL CHANGE: none. Same tool list, same dispatch, same error envelopes
  (`is_error` True/False mapping unchanged: `not envelope.get("ok", False)`).
- RISK: low, mechanical. Verified `HandlerEntry.handler` is directly awaitable with
  `(None, params)` for tests.

### 4. `InitializationOptions` construction (production)
- OLD: explicit `InitializationOptions(server_name, server_version, capabilities=ServerCapabilities(tools=ToolsCapability()))`.
- NEW: `server.create_initialization_options()` (single source of truth; name/version/
  instructions from constructor, capabilities derived from registered handlers).
- BEHAVIORAL CHANGE: none observable — same fields on the wire.
- RISK: low.

### 5. stdio entrypoint (`src/kodi_mcp_mcp/server.py`)
- UNCHANGED. `stdio_server()` + `server.run(read, write, init_options)` exist unchanged in v2.

### 6. Remote transport (`src/kodi_mcp_server/remote_mcp_app.py`)
- UNCHANGED. `StreamableHTTPSessionManager` constructor signature compatible; API-key
  ASGI wrapper unaffected. Session behavior: v2 dual-era — legacy clients keep the
  `initialize` + `Mcp-Session-Id` model (served via `serve_loop`), modern 2026-07-28
  clients use per-request envelopes; the connection era is decided by the client's
  first request. No project code assumed either era; no changes needed.

### 7. Tests (`test_mcp_artifact_tools.py`, `test_mcp_gui_tools.py`, `test_mcp_player_tools.py`)
- OLD: `await server.request_handlers[CallToolRequest](CallToolRequest(method="tools/call", params=CallToolRequestParams(...)))`
  and reads via `resp.root.content`, `resp.root.isError`, `resp.root.model_dump()`,
  `list_resp.root.tools`.
- NEW: `await server.get_request_handler("tools/call").handler(None, CallToolRequestParams(...))`
  (public lookup API + public handler callable; `None` ctx is unused by these handlers),
  reads directly on the returned result model: `resp.content`, `resp.is_error`,
  `resp.model_dump(by_alias=True)` (keeps the `"isError"` wire-key assertions valid),
  `list_resp.tools`.
- BEHAVIORAL CHANGE: none — identical requests, identical envelopes asserted.
- RISK: low. Direct handler invocation bypasses params validation (v1 tests already
  bypassed full dispatch by calling handlers directly); an end-to-end conformance test
  (new, `tests/test_mcp_v2_conformance.py`) covers the real dispatch path incl.
  initialize negotiation, discovery, and wire serialization.

### 8. Dependency metadata
- OLD: `mcp>=1.27.0,<2.0.0`
- NEW: `mcp>=2.0.0,<3.0.0` (lower bound = installed/validated release; features used —
  `add_request_handler`, `get_request_handler`, dual-era runner, `create_initialization_options` —
  all present in 2.0.0). No lockfile tracked in-repo.

## Error-semantics review (task §7)

- All tool-level failures in this project are **deliberate model-visible results**:
  handlers return `CallToolResult(is_error=True, content=[envelope JSON])` for
  invalid params, missing args, Kodi-side errors, and unexpected exceptions
  (`except Exception` → `error_type: "unknown_error"` envelope). v2 preserves this
  exactly: returning a `CallToolResult` (even `is_error=True`) is a *successful*
  JSON-RPC response carrying tool error semantics — the same contract as v1.
- The only JSON-RPC-level `ErrorData` usage is the dead "Tool not implemented"
  fallback (line ~2374): every whitelisted name is handled; it still returns a
  model-visible `CallToolResult` (not a raised protocol error), so semantics are
  unchanged.
- No handler in this project relies on raising an exception to reach the model;
  nothing needs conversion to an explicit error result. No changes required.
- Regression coverage: new conformance test asserts `is_error` propagation for
  both a known tool error and the unknown-tool fallback.

## Context / request-state review (task §8)

- No use of `server.request_context`, `get_context()`, or ambient ContextVars anywhere
  in the repo (grep: zero hits). Handlers read per-request data only from
  `params.arguments` and closed-over `runtime`. Nothing to migrate; no global
  mutable state introduced.

## Protocol-version assumptions review (task §9)

- `protocolVersion="2025-11-25"` appears only in (a) the removed `_handle_initialize`
  override (now runner-negotiated) and (b) `scripts/mcp_remote_smoke.py` (a manual
  legacy-protocol smoke client). Both remain correct: 2025-11-25 is a supported v2
  handshake version and `LATEST_HANDSHAKE_VERSION`.
- No session-persistence assumptions, no server-initiated requests, no
  `Mcp-Session-Id` handling in project code (the HTTP transport layer owns it).
- The smoke script exercises the legacy path and still works against v2
  (dual-era serving). No changes needed; compatibility is intentionally preserved.

## Categories summary

- MECHANICAL MIGRATION: handler registration, signatures, `ServerResult` unwrapping,
  `request.params` → `params`, test `.root`/`isError` reads.
- SEMANTIC/BEHAVIORAL CHANGE: `initialize` now SDK-handled (protocol-version echo vs.
  hard pin — net-identical for v1-era clients); capabilities now derived from
  handlers (wire-identical).
- TEST-ONLY DEPENDENCY ON INTERNAL API: all 31 `request_handlers[...]` test sites.
- CUSTOM MCP EXTENSION: none.
- UNCERTAIN: none remaining — every path verified against installed v2 source.
