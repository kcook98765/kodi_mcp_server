# CURRENT_STATE.md - Current State of kodi_mcp_server

**Last updated:** 2026-03-31

## Architecture Summary

### Entry Points

1. **`main.py`** — Server entry point
   - Creates combined app with `mcp_app` + `repo_app`
   - Runs via `uvicorn` on port 8000
   - Calls `validate_config()` at startup - fails fast if config missing

2. **`mcp_app.py`** — MCP-style endpoint handler
   - Receives requests, forwards to transport layer
   - Exposes `/health` and `/status` endpoints

3. **`repo_app.py`** — Repo server endpoints
   - Serves Kodi addon packages from `project/repo/`
   - Currently runs on port 8001 (configurable)

### Transport Layers

1. **`HttpJsonRpcTransport`** (`transport/http_jsonrpc.py`)
   - Sends JSON-RPC commands to Kodi over HTTP
   - Supports basic auth
   - Returns `ResponseMessage` with result, error, error_type, error_code

2. **`HttpBridgeClient`** (`transport/http_bridge.py`)
   - HTTP client for remote Kodi addon bridge
   - Methods: `get_health`, `get_status`, `get_log_tail`, `execute_addon`, etc.
   - Returns `ResponseMessage` with result, error, error_type, error_code

3. **`Base Transport`** (`transport/base.py`)
   - Abstract base class defining `send_request`, `connect`, `disconnect`

**Note:** `MockTransport` (transport/mock.py) was removed in Phase 3. Server now assumes real remote Kodi connectivity.

### Request/Response Models

**`RequestMessage`** (`models/messages.py`)
- `request_id`: string
- `command`: string
- `args`: dict

**`ResponseMessage`** (`models/messages.py`)
- `request_id`: string
- `result`: dict | None
- `error`: string | None
- `error_type`: ErrorType | None
- `error_code`: int | None

**`ErrorType`** (`models/messages.py`)
- `NETWORK_ERROR` - TCP connection failed, DNS failure
- `TIMEOUT` - Request timed out
- `AUTH_ERROR` - 401/403 credential issues
- `NOT_FOUND` - 404 resource not found
- `SERVER_ERROR` - 5xx Kodi/server error
- `PARSE_ERROR` - Invalid JSON response
- `INVALID_RESPONSE` - Response schema mismatch
- `CONFIG_ERROR` - Missing required config
- `UNKNOWN_ERROR` - Unexpected errors

### Configuration

**`config.py`**
- Loads from `project/.env` (workspace root `.env` in project dir)
- `validate_config()` function raises `ConfigError` if required values missing
- Required values: `KODI_JSONRPC_URL`, `KODI_BRIDGE_BASE_URL`
- Optional values:
  - `KODI_JSONRPC_USERNAME`, `KODI_JSONRPC_PASSWORD` (if auth required)
  - `KODI_TCP_HOST`, `KODI_TCP_PORT` (9090)
  - `KODI_WEBSOCKET_URL`
  - `KODI_TIMEOUT` (default 10)
  - `REPO_SERVER_HOST` (0.0.0.0), `REPO_SERVER_PORT` (8001)
- Authoritative repo root: `project/repo/`

### Tool Implementations

**`tools/`** directory contains:
- `jsonrpc.py` — JSON-RPC command execution
- `bridge.py` — Bridge endpoint interactions (including log retrieval)
- `addon_ops.py` — Addon lifecycle operations
- `repo.py` — Repo server operations

**Note:** `tools/logs.py` was removed in Phase 3; log retrieval now via `BridgeTool.get_bridge_log_tail()`

## Known Gaps

1. **CLI wrappers** — Not yet implemented. Future work — do not assume they exist.

2. **Status endpoint enhancement** — `/status` exists but could be more diagnostic (probes more than just connectivity)

3. **Integration tests** — No test suite exists yet. Tests would verify:
   - Config validation catches missing values
   - Transport error handling works correctly
   - Endpoint responses match documented structure

4. **Connection reuse** — New transport instance created per request (performance optimization for later)

5. **README/API docs** — Could be more aligned with current implementation (document `/status` response format, error types, etc.)

## Recent Changes

- Repository restructured to make `project/` the canonical codebase
- Config loading updated to use `project/repo/` as authoritative repo root
- Transport layers standardized around `RequestMessage`/`ResponseMessage`

## Next Steps

1. Stabilize config loading
2. Standardize error handling
3. Define CLI wrapper contract
4. Add integration tests for transports
5. Define error codes/types

---

## Definition of Done (Backend Server Changes)

For any backend server change, verify all of the following:

- [ ] No existing endpoints broken
- [ ] Responses follow standard JSON structure (`result` or `error` field)
- [ ] Error handling is consistent across all tools
- [ ] Config validation added if new config values introduced
- [ ] CURRENT_STATE.md updated (what changed, why, what gaps remain)
- [ ] Documentation updated if needed (README, SKILLS.md, etc.)
- [ ] Committed with meaningful message
- [ ] Pushed if changes should be shared (with approval)

---

## CURRENT_STATE.md Maintenance

**This file must be updated after every meaningful change.**  
If you modify code, config, or architecture, update this file before finishing.  
If no changes were made, confirm CURRENT_STATE.md is still accurate.
