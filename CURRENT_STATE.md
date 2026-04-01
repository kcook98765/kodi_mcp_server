# CURRENT_STATE.md - Current State of kodi_mcp_server

**Last updated:** 2026-03-31

## Completed Tasks (Phase 7: CLI Wrapper)

**Date:** 2026-03-31

### Summary
Implemented thin CLI wrapper `kodi-cli` that provides deterministic, machine-friendly interface to the backend server.

### Command Structure (Hierarchical)
- `kodi-cli system status` — Get server/system status
- `kodi-cli jsonrpc call --method <name>` — Execute JSON-RPC command
- `kodi-cli addon info --addonid <id>` — Get bridge addon info
- `kodi-cli addon execute --addonid <id>` — Execute addon via bridge
- `kodi-cli builtin exec --command <cmd>` — Execute Kodi builtin
- `kodi-cli log tail --lines <n>` — Get log tail from bridge

### Response Envelope (Unified)
**Success:**
```json
{
  "ok": true,
  "command": "<domain action>",
  "data": { ... },
  "latency_ms": <number if available>
}
```

**Error:**
```json
{
  "ok": false,
  "command": "<domain action>",
  "error": "<message>",
  "error_type": "<type if known>",
  "error_code": "<code if known>",
  "latency_ms": <number if available>
}
```

### Exit Codes
- 0: Success
- 1: Invalid arguments
- 2: Connection error (server unreachable)
- 3: Server error (HTTP error or invalid response)
- 4: Request timeout

### Test Coverage
- 24 tests all passing
- Validates input validation, output envelope, exit codes, error handling
- No server dependency (all mocked)

### Files Added
- `kodi-cli` — Workspace entry point (executable)
- `cli/kodi_cli.py` — Main CLI implementation
- `cli/test_cli.py` — Test suite
- `cli/pyproject.toml` — Python packaging
- `cli/requirements.txt` — Dependencies
- `cli/pytest.ini` — Test config
- `cli/README.md` — Usage documentation
- `.gitignore` — Workspace exclusions

### Architecture
```
agent → kodi-cli → backend server (localhost:8000) → remote Kodi
```

**Key principles:**
- Thin wrapper: no retry logic, no business logic duplication
- JSON-only output: all output is structured JSON
- Deterministic: same inputs = same outputs
- No state: pure HTTP passthrough with envelope wrapping

---

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
- `latency_ms`: int | None (added Phase 4 for diagnostics)

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

1. **Connection reuse** — New transport instance created per request (performance optimization for later)

2. **README/API docs** — Could be more aligned with current implementation (document `/status` response format, error types, etc.)

## Completed Tasks (Phase 5: Connection Lifecycle and Retry Boundaries)

**Date:** 2026-03-31

### Summary
Added retry logic for connection resilience with strict boundaries to improve reliability of remote Kodi communication.

### Retry Configuration
- **Max retries:** 1 (2 total attempts)
- **Retry triggers:** NETWORK_ERROR, TIMEOUT only
- **No retry:** AUTH_ERROR, NOT_FOUND, SERVER_ERROR, PARSE_ERROR, INVALID_RESPONSE, any non-whitelisted method
- **Config:** Intentionally hard-coded (no retry config values added)
- **Logging:** No retry logging (kept minimal as per Phase 5 constraints)

### Safe Retry Lists

#### HttpBridgeClient (`transport/http_bridge.py`)
**Safe methods that auto-retry on NETWORK_ERROR/TIMEOUT:**
- `get_health()`
- `get_status()`
- `get_runtime_info()`
- `get_addon_info()`
- `get_log_tail()`
- `get_log_markers()`
- `debug_ping()`

**Unsafe methods (no retry):**
- `get_file()` (explicitly excluded per Phase 5 decision)
- `write_log_marker()` — POST with side effects
- `ensure_addon_enabled()` — POST with side effects
- `execute_addon()` — POST with side effects
- `execute_builtin()` — POST with side effects
- `upload_addon_zip()` — POST with side effects
- `check_addon_version()` — read but part of deploy verification, kept manual

#### HttpJsonRpcTransport (`transport/http_jsonrpc.py`)
**Safe methods (whitelist-based, 14 methods):**
```python
SAFE_READ_METHODS = frozenset([
    "Application.GetProperties",
    "Files.GetDirectory",
    "Files.GetSources",
    "Player.GetActivePlayers",
    "Player.GetItem",
    "VideoLibrary.GetMovies",
    "VideoLibrary.GetTVShows",
    "VideoLibrary.GetRecentlyAddedMovies",
    "Addons.GetAddons",
    "Addons.GetAddonDetails",
    "Settings.GetSettingValue",
    "System.GetProperties",
    "JSONRPC.Version",
    "JSONRPC.Introspect",
])
```

**Unsafe methods (anything not in whitelist):**
- `Addons.SetAddonEnabled` — SET operation
- `Addons.ExecuteAddon` — EXECUTE operation
- `System.Reboot`, `System.Shutdown` — mutating operations

### Implementation Details
- Added `_retry_wrapper()` helper method to both transports
- HttpJsonRpcTransport: whitelist-based detection via `is_safe_to_retry()` function
- Added `_send_once()` for non-retry request execution (separation of concerns)
- Added `_error_response()` method to HttpJsonRpcTransport (was missing)

### Test Coverage Added
- **test_http_errors.py** — 6 new retry behavior tests:
  - `test_safe_method_retries_on_timeout` — Verify retry on timeout for safe methods
  - `test_mutating_method_does_not_retry_on_timeout` — Verify no retry for mutating methods
  - `test_auth_error_does_not_retry` — Verify no retry on AUTH_ERROR
  - `test_network_error_retries_once` — Verify retry on network errors
  - `test_max_one_retry` — Verify exactly 1 retry (2 total attempts)
  - `test_is_safe_to_retry` — Verify whitelist detection

### Test Results
- **Total:** 17 tests (up from 14)
- **Passed:** 17
- **Failed:** 0

### Files Changed in project/
1. `src/kodi_mcp_server/transport/http_bridge.py` (~90 lines changed)
2. `src/kodi_mcp_server/transport/http_jsonrpc.py` (~130 lines changed)
3. `tests/test_http_errors.py` (~160 lines changed)

---

## Completed Tasks (Phase 4: Diagnostics + Test Coverage)

**Date:** 2026-03-31

### Test Setup
- Created `tests/` directory with pytest configuration
- Added `requirements-dev.txt` (pytest, pytest-mock, httpx)
- Created `pytest.ini` for test configuration
- Created `.env.test` as template for test environment

### ResponseMessage Enhancements
- Added `latency_ms` field for request latency tracking (diagnostics)
- Removed `error_message` field (determined redundant with `error`)
- Updated `to_dict()` and `from_dict()` for final field set

### Final ResponseMessage Fields
- `request_id`: string
- `result`: dict | None
- `error`: string | None
- `error_type`: ErrorType | None
- `error_code`: int | None
- `latency_ms`: int | None (diagnostics)

### Test Coverage Added
1. **test_config.py** — Config validation tests (1 test)
   - Success when all values present (env loaded from .env)

2. **test_models.py** — ResponseMessage serialization tests (6 tests)
   - Success response serialization
   - Error response serialization
   - from_dict parsing (success and error)
   - Unknown error type handling
   - RequestMessage serialization

3. **test_http_errors.py** — HTTP error mapping tests (7 tests)
   - HTTP 401 → AUTH_ERROR
   - HTTP 403 → AUTH_ERROR
   - HTTP 404 → NOT_FOUND
   - HTTP 5xx → SERVER_ERROR
   - Error code preservation
   - Explicit UNKNOWN_ERROR usage

### Transport Layer Updates
- HttpJsonRpcTransport tracks `latency_ms` for all responses
- Error responses use existing `error` field (no separate `error_message`)
- `_error_response()` helper updated to accept `latency_ms`

### Test Results
- **Total:** 14 tests
- **Passed:** 14
- **Failed:** 0

### Existing Tests (from previous work)
- **test_endpoints.py** — Endpoint structure tests
  - `/health` returns valid JSON
  - `/status` returns expected nested structure
  - Config section indicates loaded state
  - JSON-RPC and bridge sections with status and URL

- **test_http_jsonrpc.py** — Transport error handling tests
  - Error responses include error_type
  - Error responses include error_code for HTTP errors
  - Default to UNKNOWN_ERROR

---

## Recent Changes

- Phase 5: Connection lifecycle and retry boundaries completed
- Repository restructured to make `project/` the canonical codebase
- Config loading updated to use `project/repo/` as authoritative repo root
- Transport layers standardized around `RequestMessage`/`ResponseMessage`

## Next Steps

1. **Integration testing** — Test CLI wrapper against live remote Kodi instance to verify end-to-end flow
2. **Connection reuse** — Defer to Phase 8 or later. New transport instance created per request (performance optimization).
3. **README/API docs** — Document `/status` response format, error types, CLI usage patterns.
4. **Production validation** — Verify CLI works reliably across network conditions and Kodi states.

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
---

## Completed Tasks (Phase 6: Transport Cleanup)

**Date:** 2026-03-31

### Summary
Unified error handling across transport layer, reduced code duplication, added latency_ms tracking.

### Key Changes

#### HttpBridgeClient (`transport/http_bridge.py`)
- Added `_make_request()` — unified HTTP request with error handling and latency tracking
- Added `_http_code_to_error()` helper — consistent HTTP error type mapping  
- Added `_url_error_to_response()` helper — consistent URL error handling
- Removed: `_get_json()`, `_post_json()`, `_post_bytes()` (replaced by `_make_request()`)
- Removed: `_handle_http_error()`, `_handle_url_error()` (replaced by helpers)
- Updated: All methods now use unified error handling with latency_ms tracking
- Refactored: 170 lines → 139 lines (net -31 lines)

#### HttpJsonRpcTransport (`transport/http_jsonrpc.py`)
- No changes needed — already well-structured
- Retry logic preserved as-is
- Error handling aligned with Phase 5

### Implementation Approach
- No new shared modules (per Phase 6 constraint)
- Helpers kept local to transport files
- Maintained ResponseMessage contract unchanged
- Preserved retry behavior exactly

### Test Results
- **Total:** 17 tests (unchanged)
- **Passed:** 17
- **Failed:** 0

---
