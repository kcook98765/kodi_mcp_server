# kodi_mcp_server

Custom Python middle-layer server for remote Kodi integration.

## Purpose

This project provides a HTTP backend server that exposes Kodi-related operations as structured JSON-RPC and bridge endpoints. It sits between local CLI tools and remote Kodi, normalizing remote Kodi/bridge interactions into deterministic, machine-friendly responses.

## Architecture

```
agent → kodi-cli → kodi_mcp_server (localhost:8000) → remote Kodi
```

**Layering:**
- `kodi-cli` (Phase 7) — Thin CLI wrapper with unified JSON envelope
- `kodi_mcp_server` (this repo) — HTTP endpoints, transport layers, tools
- Remote Kodi — Bridge addon endpoints + JSON-RPC

## Quick Start

**Server setup:**
```bash
cd /home/node/.openclaw/workspace/project
# Configure in .env:
#   KODI_JSONRPC_URL=http://kodi.local:8080/jsonrpc
#   KODI_BRIDGE_BASE_URL=http://kodi.local:8765
#   KODI_JSONRPC_USERNAME=kodi
#   KODI_JSONRPC_PASSWORD=kodi
uvicorn kodi_mcp_server.main:app --port 8000 --host 0.0.0.0
```

**CLI wrapper:**
```bash
# From workspace root:
./kodi-cli system status
./kodi-cli jsonrpc call --method JSONRPC.Version
./kodi-cli addon info --addonid plugin.video.example
./kodi-cli addon execute --addonid plugin.video.example
./kodi-cli builtin exec --command PlayerControl(Play)
./kodi-cli log tail --lines 20
```

## CLI Command Structure

**Hierarchical commands:**
- `kodi-cli system status` — Get server/system status
- `kodi-cli jsonrpc call --method <name>` — Execute JSON-RPC command
- `kodi-cli addon info --addonid <id>` — Get bridge addon info
- `kodi-cli addon execute --addonid <id>` — Execute addon via bridge
- `kodi-cli builtin exec --command <cmd>` — Execute Kodi builtin
- `kodi-cli log tail --lines <n>` — Get log tail from bridge

**Unified JSON response envelope:**
- Success: `{"ok": true, "command": "...", "data": {...}, "latency_ms": ...}`
- Error: `{"ok": false, "command": "...", "error": "...", "error_type": ..., "error_code": ...}`

**Exit codes:** 0=success, 1=invalid args, 2=connection, 3=server, 4=timeout

**Full CLI code:** Located in `scripts/kodi_cli.py` (this repo).

## Server Endpoints

**Health & Status:**
- `GET /health` — Basic health check
- `GET /status` — Full system status (server, config, JSON-RPC, bridge connectivity)

**Tools (all return structured JSON):**
- `GET /tools/execute_jsonrpc?method=<name>` — Execute JSON-RPC method
- `GET /tools/get_bridge_addon_info?addonid=<id>` — Get addon info
- `POST /tools/execute_bridge_addon` — Execute addon
- `POST /tools/execute_bridge_builtin` — Execute builtin command
- `GET /tools/get_bridge_log_tail?lines=<n>` — Get log tail
- `GET /tools/get_bridge_health` — Check bridge health
- `GET /tools/get_bridge_status` — Get bridge status
- `GET /tools/get_addons` — List all addons
- `GET /tools/get_addon_details?addonid=<id>` — Get addon details
- `GET /tools/list_addons` — List addons with filters
- `GET /tools/execute_addon?addonid=<id>&wait=<bool>` — Execute addon
- `GET /tools/is_addon_installed?addonid=<id>` — Check installation
- `GET /tools/is_addon_enabled?addonid=<id>` — Check enabled state
- `GET /tools/ensure_addon_enabled?addonid=<id>` — Ensure enabled
- `POST /tools/publish_addon_to_repo` — Publish addon to repo
- `GET /tools/get_recent_movies?limit=<n>` — Get recent movies
- `GET /tools/get_tvshows_sample?limit=<n>` — Get TV shows sample
- `GET /tools/get_application_properties` — Get application properties
- `GET /tools/get_active_players` — Get active players
- `GET /tools/get_player_item` — Get current player item
- `GET /tools/get_system_properties` — Get system properties
- `GET /tools/get_setting_value?setting=<name>` — Get setting value
- `GET /tools/get_jsonrpc_version` — Get JSON-RPC version
- `GET /tools/introspect_jsonrpc` — Introspect JSON-RPC interface
- `POST /tools/ensure_bridge_addon_enabled` — Ensure bridge addon enabled
- `POST /tools/restart_bridge_addon` — Restart bridge addon
- `POST /tools/update_addon` — Update addon
- `GET /tools/wait_for_addon_version` — Wait for specific version

## Configuration

Environment variables (in `project/.env`):

**Required:**
- `KODI_JSONRPC_URL` — Kodi JSON-RPC endpoint (e.g., `http://kodi.local:8080/jsonrpc`)
- `KODI_BRIDGE_BASE_URL` — Remote Kodi bridge endpoint (e.g., `http://kodi.local:8765`)

**Optional:**
- `KODI_JSONRPC_USERNAME` — Basic auth username for JSON-RPC
- `KODI_JSONRPC_PASSWORD` — Basic auth password for JSON-RPC
- `KODI_TIMEOUT` — Request timeout in seconds (default: 10)

## Testing

**Backend unit tests (mocked, no Kodi dependency):**
```bash
cd project
python -m pytest tests/ -v
```

**Test coverage:**
- `test_config.py` — Configuration validation (1 test)
- `test_http_errors.py` — HTTP error handling and mapping (12 tests)
- `test_http_jsonrpc.py` — JSON-RPC transport error handling (3 tests)
- `test_models.py` — RequestMessage/ResponseMessage serialization (6 tests)
- Total: 23 tests, all passing

**CLI wrapper tests:**
```bash
cd project
python -m pytest tests/test_cli.py -v
```

- `test_cli.py` — CLI input validation, output envelope, exit codes (24 tests)

**Live integration testing (future):**
- End-to-end testing against real remote Kodi instance
- Network resilience validation
- Performance profiling under load

## Development Notes

**Key principles:**
- **Remote-only:** All Kodi communication goes through remote bridge/JSON-RPC
- **Structured responses:** All output is deterministic JSON
- **No business logic in CLI:** CLI is thin wrapper only
- **Thin transport layer:** HTTP clients with standardized error handling
- **Retry boundaries:** Strict retry rules for safe vs unsafe operations

**File structure:**
```
project/
├── src/kodi_mcp_server/
│   ├── main.py            — Server entry point
│   ├── mcp_app.py         — MCP-style endpoints
│   ├── repo_app.py        — Repo server endpoints
│   ├── app_shared.py      — Shared app configuration
│   ├── config.py          — Configuration loading
│   ├── paths.py           — Path constants
│   ├── models/
│   │   └── messages.py    — Request/ResponseMessage, ErrorType
│   ├── transport/
│   │   ├── base.py        — Base transport class
│   │   ├── http_jsonrpc.py — HTTP JSON-RPC transport
│   │   └── http_bridge.py  — HTTP bridge client
│   └── tools/
│       ├── jsonrpc.py     — JSON-RPC tool implementation
│       ├── bridge.py      — Bridge tool implementation
│       ├── addon_ops.py   — Addon lifecycle operations
│       └── repo.py        — Repo server operations
├── tests/                 — Unit tests (mocked)
├── scripts/               — Build/deploy scripts
│   └── kodi_cli.py        — CLI wrapper (Phase 7)
└── repo/                  — Authoritative Kodi addon repo
```

## CLI Wrapper

The CLI wrapper provides a thin, deterministic interface to the backend server.

**Location:** `scripts/kodi_cli.py` (this repo)

**Usage:**
```bash
# From workspace root
cd /home/node/.openclaw/workspace
./kodi-cli system status
./kodi-cli jsonrpc call --method JSONRPC.Version
./kodi-cli addon info --addonid plugin.video.example
./kodi-cli addon execute --addonid plugin.video.example
./kodi-cli builtin exec --command PlayerControl(Play)
./kodi-cli log tail --lines 20
```

**Server Configuration:**
- Default server: `http://localhost:8000`
- Override with `--server`:
  ```bash
  kodi-cli --server http://my-kodi-server:8000 system status
  ```

**Output Format:**
- All commands output structured JSON
- Success: `{"ok": true, "command": "...", "data": {...}, "latency_ms": ...}`
- Error: `{"ok": false, "command": "...", "error": "...", "error_type": ..., "error_code": ...}`
- Use `--compact` for inline JSON (no indentation):
  ```bash
  kodi-cli --compact jsonrpc call --method JSONRPC.Version
  ```

**Exit Codes:**
| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Invalid arguments |
| 2 | Connection error (server unreachable) |
| 3 | Server error (HTTP error or invalid response) |
| 4 | Request timeout |

**Architecture:**
```
agent → kodi-cli → backend server (localhost:8000) → remote Kodi
```

No business logic is duplicated here. All operations delegate to the server's tool endpoints.

## MCP Wrapper (VS Code / Cline)

This repo also contains a **stdio-based MCP server wrapper** that exposes a curated subset of the HTTP `/tools/*` endpoints to VS Code/Cline via the Model Context Protocol.

### Prerequisites

1. Run the backend HTTP server (this project) on a reachable URL (default `http://localhost:8000`).
2. Install this project so the console scripts are available.

### Environment

- `KODI_MCP_BASE_URL` (optional)
  - Default: `http://localhost:8000`
  - Purpose: Base URL of the running `kodi_mcp_server` FastAPI backend.

### Run the MCP wrapper manually

With the backend already running:

```bash
# Windows PowerShell
$env:KODI_MCP_BASE_URL = "http://localhost:8000"
kodi-mcp-wrapper
```

```bash
# macOS/Linux
export KODI_MCP_BASE_URL="http://localhost:8000"
kodi-mcp-wrapper
```

The wrapper speaks MCP over **stdin/stdout**, so it will appear to “hang” when run directly. That’s expected; VS Code/Cline will manage the process.

### Configure in Cline (stdio)

Add an entry to your `cline_mcp_settings.json` `mcpServers` section.

**Example (local backend):**

```json
{
  "mcpServers": {
    "kodi-mcp": {
      "command": "kodi-mcp-wrapper",
      "args": [],
      "env": {
        "KODI_MCP_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

**Example (remote backend):**

```json
{
  "mcpServers": {
    "kodi-mcp": {
      "command": "kodi-mcp-wrapper",
      "args": [],
      "env": {
        "KODI_MCP_BASE_URL": "http://<your-server-host>:8000"
      }
    }
  }
}
```

### First connection test

After Cline connects, try these tools first:

1. `kodi_status`
2. `bridge_health`
3. `bridge_runtime_info`

Notes:
- If you’re developing locally without installing the package, you can set `command` to `python` and point `args` at `src/kodi_mcp_wrapper/server.py`.
- The wrapper currently implements only a subset of tools and uses the backend for execution.

## Next Steps

1. **Live integration testing** — Validate CLI + server against real remote Kodi
2. **Connection reuse** — Optimize transport with connection pooling
3. **README/API docs** — Expand documentation with examples
4. **Production validation** — Test across network conditions and Kodi states
