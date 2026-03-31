# CURRENT_STATE.md - Current State of kodi_mcp_server

**Last updated:** 2026-03-31

## Architecture Summary

### Entry Points

1. **`main.py`** — Server entry point
   - Creates combined app with `mcp_app` + `repo_app`
   - Runs via `uvicorn` on port 8000

2. **`mcp_app.py`** — MCP-style endpoint handler
   - Receives requests, forwards to transport layer

3. **`repo_app.py`** — Repo server endpoints
   - Serves Kodi addon packages from `project/repo/`
   - Currently runs on port 8001 (configurable)

### Transport Layers

1. **`HttpJsonRpcTransport`** (`transport/http_jsonrpc.py`)
   - Sends JSON-RPC commands to Kodi over HTTP
   - Supports basic auth
   - Returns `ResponseMessage` with result or error

2. **`HttpBridgeClient`** (`transport/http_bridge.py`)
   - HTTP client for remote Kodi addon bridge
   - Methods: `get_health`, `get_status`, `get_file`, `execute_addon`, etc.
   - Returns `ResponseMessage` with result or error

3. **`MockTransport`** (`transport/mock.py`)
   - Placeholder for testing
   - Returns mock responses

4. **`Base Transport`** (`transport/base.py`)
   - Abstract base class defining `send_request`, `connect`, `disconnect`

### Request/Response Models

**`RequestMessage`** (`models/messages.py`)
- `request_id`: string
- `command`: string
- `args`: dict

**`ResponseMessage`** (`models/messages.py`)
- `request_id`: string
- `result`: dict | None
- `error`: string | None

### Configuration

**`config.py`**
- Loads from environment variables
- Also loads from `.env` in `project/mcp_repo_server/` (if exists)
- Key settings:
  - `KODI_JSONRPC_URL`, `KODI_JSONRPC_USERNAME`, `KODI_JSONRPC_PASSWORD`
  - `KODI_TCP_HOST`, `KODI_TCP_PORT` (9090)
  - `KODI_WEBSOCKET_URL`
  - `KODI_BRIDGE_BASE_URL`
  - `REPO_SERVER_HOST` (0.0.0.0), `REPO_SERVER_PORT` (8001)
- Authoritative repo root: `project/repo/`

### Tool Implementations

**`tools/`** directory contains:
- `jsonrpc.py` — JSON-RPC command execution
- `bridge.py` — Bridge endpoint interactions
- `addon_ops.py` — Addon lifecycle operations
- `repo.py` — Repo server operations
- `logs.py` — Log retrieval

## Known Gaps

1. **Config loading** — Currently only loads from `mcp_repo_server/.env`. Should support workspace root or explicit path.

2. **Error handling consistency** — Some transports return structured errors, others return raw exceptions.

3. **Mock transport** — Needs implementation for testing.

4. **CLI wrappers** — Not yet implemented. Future work.

5. **Documentation** — README exists but needs updating to reflect current architecture.

## Recent Changes

- Repository restructured to make `project/` the canonical codebase
- Config loading updated to use `project/repo/` as authoritative repo root
- Transport layers standardized around `RequestMessage`/`ResponseMessage`

## Next Steps

1. Stabilize config loading
2. Add integration tests for transports
3. Define CLI wrapper contract
4. Update README with architecture diagram
5. Document tool endpoints and expected inputs/outputs

---

Use this file to track progress. Update it as you make changes.
