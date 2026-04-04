# CURRENT_STATE.md - Current State of kodi_mcp_server

**Last updated:** 2026-03-31

## Architecture Overview

### Three-Surface Design

The kodi_mcp_server exposes three distinct operational surfaces:

```
┌─────────────────────────────────────────────────────────────────┐
│ kodi_mcp_server (localhost:8000)                                │
│                                                                 │
│  /runtime/*     ← Runtime surface (execute, status, logs)       │
│  /diagnostic/*  ← Advisory surface (read-only metadata)         │
│  /dev-loop/*    ← Dev-loop surface (build, publish, verify)    │
└─────────────────────────────────────────────────────────────────┘
```

**Key principle:** Runtime surface operates ONLY on already-installed addons. Dev-loop surface operates on build artifacts and repo server. Manual install checkpoint required between stages 2 and 4.

### Runtime vs Dev-Loop Boundary

- **Runtime surface** — Execute, status, logs, enable/disable of installed addons. All tools assume addon is already present on remote Kodi.
- **Dev-loop surface** — Build addon ZIP, publish to local repo server, verify deployed version. Requires HUMAN MANUAL INSTALL step on remote Kodi after publish.
- **Advisory/diagnostic surface** — Read-only metadata. No side effects.

**Critical constraint:** Repo state ≠ Installed addon state. Repo may have newer version, but installed addon only changes via human manual install on remote Kodi.

### Dev-Loop Workflow (Stages 1-5)

```
Stage 1: build/package → build_addon_package (automated)
  ↓
Stage 2: publish to repo server → publish_addon_to_repo (automated)
  ↓
[HUMAN MANUAL INSTALL CHECKPOINT]
  ↓
Stage 3: Kodi repo refresh → trigger_repo_refresh (human-gated, no bridge endpoint)
  ↓
Stage 4: addon update/install → update_addon (human-gated, manual ZIP install)
  ↓
Stage 5: runtime verification → verify_bridge_addon_deploy (automated)
```

**Automated stages:** 1, 2, 5
**Human-gated stages:** 3, 4 (requires manual action on remote Kodi)

---

## Tool Surface Classification

### Runtime Surface (Core Operations)

All runtime tools operate on already-installed addons. Assumes addon exists on remote Kodi.

**Core runtime tools:**
- `execute_bridge_addon` — Execute addon via bridge
- `execute_addon` — Execute addon via JSON-RPC
- `ensure_bridge_addon_enabled` — Enable addon (conditional: requires addon installed)
- `ensure_addon_enabled` — Enable addon via JSON-RPC (conditional)
- `get_bridge_log_tail` — Read logs
- `write_bridge_log_marker` — Write trace marker
- `execute_bridge_builtin` — Execute Kodi builtin
- `get_addons` — List all addons
- `get_addon_details` — Get addon metadata
- `list_addons` — Filtered addon listing
- `service_status` — Service addon metadata

**Classification:** Runtime-safe. All these tools have side effects on installed addons but assume addon already present.

### Advisory/Diagnostic Surface (Read-Only)

No side effects. Pure metadata queries.

**Advisory tools:**
- `get_bridge_version` — Read bridge version (cannot enforce)
- `get_bridge_runtime_info` — Runtime metadata
- `get_bridge_file` — Read file from Kodi
- `get_bridge_addon_info` — Addon metadata via bridge
- `get_bridge_log_markers` — Log markers
- `get_bridge_control_capabilities` — Capabilities list
- `check_bridge_addon_version` — Check version (cannot enforce update)
- `is_addon_installed` — Read-only check
- `is_addon_enabled` — Read-only check
- `run_addon_and_report` — Execute and report events (cannot force)

**Classification:** Advisory-only. These tools can report state but cannot change it.

### Dev-Loop Surface (Lifecycle Management)

Dev-loop operates on build artifacts and repo server. Requires human manual install step.

**Dev-loop tools:**
- `build_addon_package` — Build addon ZIP (automated)
- `publish_addon_to_repo` — Publish ZIP to local repo (automated)
- `upload_bridge_addon_zip` — Serve ZIP from local repo (human-gated: remote install required)
- `update_addon` — Check repo for updates, report availability (hybrid: can detect but not force install)
- `restart_bridge_addon` — Restart via JSON-RPC Disable/Enable (hybrid)
- `verify_bridge_addon_deploy` — Verify installed vs expected version (human-gated: validation only)

**Classification:** Dev-loop with human checkpoint. Stages 1-2 automated; stages 3-4 human-gated; stage 5 automated.

### Tools Removed/Deprecated

- `tools/verify_bridge_addon_deploy` — Removed from runtime surface, moved to dev-loop (human-gated)
- `tools/upload_bridge_addon_zip` — Removed from runtime surface, moved to dev-loop (human-gated)
- `tools/update_addon` — Removed from runtime surface, moved to dev-loop (human-gated)
- `tools/wait_for_addon_version` — Removed (assumes version enforcement not possible)

---

## Completed Tasks (Phase 7: CLI Wrapper)

### Command Structure (Hierarchical)
- `kodi-cli system status` — Get server/system status
- `kodi-cli jsonrpc call --method <name>` — Execute JSON-RPC command
- `kodi-cli addon info --addonid <id>` — Get bridge addon info
- `kodi-cli addon execute --addonid <id>` — Execute addon via bridge
- `kodi-cli builtin exec --command <cmd>` — Execute Kodi builtin
- `kodi-cli log tail --lines <n>` — Get log tail from bridge
- `kodi-cli service status --addonid <id>` — Get service metadata (Phase 7.1a)

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
- `project/scripts/kodi_cli.py` — Main CLI implementation
- `project/tests/test_cli.py` — Test suite
- `project/scripts/pyproject.toml` — Python packaging
- `project/requirements-cli.txt` — Dependencies
- `project/pytest.ini` — Test config (merged)
- `cli/README.md` — Merged into `project/README.md`
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

## Completed Tasks (Phase 7.2: Repo Generator + Repository Addon Support)

**Date:** 2026-04-03

### Summary
Added infrastructure to build and package Kodi repository addons that point at the server-served repository. Enables Kodi to discover and install addons from the local repo server.

### New Files Added

#### `project/src/kodi_mcp_server/repo_generator.py` (~250 lines)
Repository addon packaging utilities with:
- **`load_addons_xml()`** — Parse addons.xml from repo root
- **`render_template()`** — Jinja2 template rendering for addon.xml
- **`build_repo_addon()`** — Build installable repository addon ZIP
- **`generate_addons_xml_gz()`** — Create addons.xml.gz for repository serving
- **`RepoGeneratorError`** — Exception class for build failures

#### `project/templates/` (new directory)
- **`addon.xml`** — Template for repository.kodi-mcp addon
- **`repository.xml`** — Template for repository metadata

### Config Changes

#### `project/src/kodi_mcp_server/config.py`
- Added `DEFAULT_REPO_BASE_URL = "http://localhost:8001"`
- Added `REPO_BASE_URL` environment variable support

### Endpoint Changes

#### `project/src/kodi_mcp_server/repo_server.py`
- Added `/repo/health` detailed endpoint showing:
  - Repo base URL and root path
  - Addon count and first 20 addon IDs
  - addons.xml.gz existence check

### New Capabilities

1. **Repository addon generation** — Build installable `repository.kodi-mcp` addon
2. **Template-based configuration** — Jinja2 templates for repo URL injection
3. **Repository health inspection** — Debug repo visibility for Kodi
4. **Checksum generation** — SHA256 checksums for addon integrity

### Dev-Loop Integration

Repository addon is used in **Stage 6** (not yet automated):
```
Stage 1-5: Build, publish, manual install (existing workflow)
  ↓
Stage 6: Build repository.kodi-mcp addon
  ↓
Stage 7: Human installs repository.kodi-mcp on Kodi
  ↓
Stage 8: Kodi discovers repo, addons appear in browser
```

**Note:** Repository addon integration is conceptual. Requires human manual install similar to addon update workflow.

### Current State

- **Server:** Running, responding to health checks
- **Config:** Updated with REPO_BASE_URL
- **Git:** project/ has uncommitted changes (config.py, repo_server.py, repo_generator.py, templates/)
- **CURRENT_STATE.md:** Updated for Phase 7.2

---

## Completed Tasks (Phase 7.1a: Service Detection + Live Validation)

**Date:** 2026-03-31

### Summary
Implemented service addon detection in `addon execute` and added `service status` command for metadata inspection.

### Changes Made

#### `tools/addon_ops.py`
- Added service detection in `addon_execute` — returns `invalid_operation` error if target is a service addon
- Behavior: `addon execute` now correctly rejects service addons with semantic error

#### `tools/service_ops.py` (new file)
- Added `service_status` command — returns metadata-based status (not runtime liveness)
- Provides info about service addon without executing it

#### CLI Wrapper Updates
- New command: `kodi-cli service status --addonid <id>`
- Returns service metadata from Kodi JSON-RPC

### Live Validation Complete

#### Test 1: Addon Execute Rejection
- **Scenario:** Execute service addon via `kodi-cli addon execute`
- **Result:** Returns `invalid_operation` error ✓
- **Wording:** Semantic error (not connection/timeout error)

#### Test 2: Service Status
- **Scenario:** Query service via `kodi-cli service status`
- **Result:** Returns metadata-based status ✓
- **Clarification:** Shows addon metadata (version, path, enabled state) — not runtime liveness

### Key Clarifications

**Add-on execute:**
- Returns semantic `invalid_operation` for service addons
- This is a validation error, not a runtime failure

**Service status:**
- Returns metadata (version, enabled state, etc.)
- Does NOT indicate if service is currently running (no control surface on addon side yet)
- Next step: Add ping/health/version endpoints to bridge addon for runtime introspection

### Files Changed in project/
1. `src/kodi_mcp_server/tools/addon_ops.py` — Service detection logic
2. `src/kodi_mcp_server/tools/service_ops.py` — New file, service status command
3. `scripts/kodi_cli.py` — Added `service status` command

---

### Command Structure (Hierarchical)
- `kodi-cli system status` — Get server/system status
- `kodi-cli jsonrpc call --method <name>` — Execute JSON-RPC command
- `kodi-cli addon info --addonid <id>` — Get bridge addon info
- `kodi-cli addon execute --addonid <id>` — Execute addon via bridge
- `kodi-cli builtin exec --command <cmd>` — Execute Kodi builtin
- `kodi-cli log tail --lines <n>` — Get log tail from bridge
- `kodi-cli service status --addonid <id>` — Get service metadata (Phase 7.1a)

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

1. **Addon control surface** — Add ping/health/version endpoints to bridge addon for runtime introspection
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

## Documentation Index

- **CURRENT_STATE.md** — This file, project state and task tracking
- **PROJECT.md** — Architecture overview, OpenClaw TOP roles and rules
- **dev_loop_workflow.md** — Dev-loop stages 1-5, human-gated workflow
- **README.md** — Quick start and testing instructions

## Key Documents

### Three-Surface Design

- **Runtime surface** (`/runtime/*`): Execute, status, logs, enable/disable
- **Advisory surface** (`/diagnostic/*`): Read-only metadata, version checks
- **Dev-loop surface** (`/dev-loop/*`): Build, publish, verify

### Dev-Loop Workflow

See **dev_loop_workflow.md** for detailed 5-stage workflow:
1. Build package (automated)
2. Publish to repo (automated)
3. Kodi repo refresh (human-gated)
4. Addon update/install (human-gated)
5. Runtime verification (automated)

### OpenClaw TOP Rules

See **PROJECT.md** for:
- When to use each surface
- When to require git commit/push
- When to require addon.xml version bump
- When to stop for manual install
- Worker handoff rules

### Server-Only Dev-Loop Model (Frozen)

**IMPORTANT:** kodi_mcp_server packages an **internal test addon** for repo/update workflow validation. It does NOT package the real Kodi addon (kodi_mcp_addon).

**Authoritative paths for server dev-loop:**
- **Test addon source:** `/home/node/.openclaw/workspace/project/service.kodi_mcp/`
- **Version source:** `/home/node/.openclaw/workspace/project/service.kodi_mcp/addon.xml`
- **Build output:** `/home/node/.openclaw/workspace/addon/service.kodi_mcp-*.zip`
- **Repo publish:** `/home/node/.openclaw/workspace/repo/dev-repo/zips/service.kodi_mcp/`
- **External (IGNORE):** `/home/node/.openclaw/workspace/kodi_addon/packages/service.kodi_mcp/` — real addon project, out of server's packaging scope

**Dev-loop sequence:**
1. Read version from `project/service.kodi_mcp/addon.xml`
2. Build/package from `project/service.kodi_mcp/` source
3. Publish ZIP to `repo/dev-repo/zips/service.kodi_mcp/`
4. **STOP** — human manual install on remote Kodi required before proceeding

**Critical constraint:** kodi_mcp_server must NOT package kodi_addon. The server's dev-loop is for internal validation only. Real Kodi addon (kodi_mcp_addon) is a separate project.
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
