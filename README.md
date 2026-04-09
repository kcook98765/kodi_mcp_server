# Kodi MCP Server

This repository provides an **MCP (Model Context Protocol) server for Kodi**.

It exposes a curated set of Kodi operations (Kodi JSON-RPC + the Kodi MCP bridge addon) as MCP tools, so agent clients (like VS Code/Cline) can control and inspect Kodi in a structured way.

## Quick start

**First-time onboarding (repo install)**
1) Install + enable the Kodi bridge addon: `service.kodi_mcp`
2) Set the shared token in Kodi addon settings (**service.kodi_mcp → Kodi MCP → MCP shared token**)
3) Start this server
4) Wait briefly: the server will **auto-register** and **auto-stage the dev repo zip**; then in Kodi run **Developer setup → Install from zip**

**Managed addon loop (after repo is installed in Kodi)**
1) Register local addon: `managed_addon_register`
2) Build/publish/stage/apply: `managed_addon_build_publish_stage_and_apply`
3) If needed: `managed_addon_validate_state`

Success = `verification.apply_verified == true`
Retry only if `verification.can_retry == true`

## Kodi addon requirement

- This MCP server requires the Kodi bridge addon to be installed and enabled in Kodi:
  https://github.com/kcook98765/kodi_mcp_addon
- The addon exposes the HTTP bridge used by this server and provides the Developer setup flow for first-time repo installation.
- After the addon token is configured and this server starts, the server will automatically register and stage the dev repo zip for first-time install.
- Without the addon, the MCP server cannot talk to Kodi.

## Connection modes

### 1) MCP over stdio (default / most compatible)

Run by your MCP client (Cline) as a local process:

- Command: `kodi-mcp`
- Transport: stdin/stdout

**Cline config (stdio)**

```json
{
  "mcpServers": {
    "kodi-mcp": {
      "command": "kodi-mcp",
      "args": [],
      "env": {
        "KODI_JSONRPC_URL": "http://kodi.local:8080/jsonrpc",
        "KODI_BRIDGE_BASE_URL": "http://kodi.local:8765"
      }
    }
  }
}
```

### 2) MCP remote transport (Streamable HTTP)

Expose the MCP server over HTTP at:

- `http://<host>:8010/mcp`

The MCP server runs on the host and clients connect over HTTP; **no local process is required**.

Start the server:

```bash
uvicorn kodi_mcp_server.main:app --host 0.0.0.0 --port 8010
```

#### Cline config (remote MCP)

Example **with API key header**:

```json
{
  "mcpServers": {
    "kodi-mcp-remote": {
      "type": "streamableHttp",
      "url": "http://<server-host>:8010/mcp",
      "disabled": false,
      "headers": {
        "x-mcp-api-key": "<optional>"
      }
    }
  }
}
```

Example **without headers** (no API key):

```json
{
  "mcpServers": {
    "kodi-mcp-remote": {
      "type": "streamableHttp",
      "url": "http://<server-host>:8010/mcp",
      "disabled": false
    }
  }
}
```

#### API key (optional)

To require an API key for remote MCP requests, set:

- `MCP_API_KEY=<your key>`

Clients must send:

- `x-mcp-api-key: <your key>`

> Tip (Windows/cmd.exe): use `set "MCP_API_KEY=secret"` to avoid accidental trailing spaces.

### 3) Optional HTTP debug/compatibility endpoints

The same FastAPI app also exposes HTTP endpoints under `/health`, `/status`, and `/tools/*`.

These are useful for debugging and for the included CLI wrapper, but MCP (stdio or remote) is the primary interface.

## Configuration

Required environment variables:
- `KODI_JSONRPC_URL` (e.g. `http://kodi.local:8080/jsonrpc`)
- `KODI_BRIDGE_BASE_URL` (e.g. `http://kodi.local:8765`)

Optional:
- `KODI_JSONRPC_USERNAME`, `KODI_JSONRPC_PASSWORD`
- `KODI_TIMEOUT`
- `MCP_API_KEY` (remote MCP only)

## First connection test (stdio or remote)

Once connected, try these MCP tools first:
1. `kodi_status`
2. `bridge_health`
3. `bridge_runtime_info`

If you’re testing the **remote** transport directly, you can also do a minimal curl initialize:

```bash
curl -i -N http://<server-host>:8010/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-mcp-api-key: <optional>" \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## Managed addon development workflow (golden path)

### Prerequisites
- `KODI_BRIDGE_BASE_URL` set (bridge addon HTTP base URL)
- `KODI_BRIDGE_TOKEN` set
  - Must match Kodi addon setting: **service.kodi_mcp → mcp_token**
- Kodi is running with **service.kodi_mcp enabled**

Note: first-time repo installation no longer requires a separate staging action — the server auto-stages the current dev repo zip once registration is healthy.

### Tool call sequence (MCP)

1) Register the local addon source folder (must contain `addon.xml`):
```json
{ "source_path": "C:/dev/addons/plugin.video.foo" }
```

2) Build → publish into dev repo → build dev repo zip → stage to Kodi:
```json
{
  "managed_addon_id": "plugin.video.foo",
  "version_policy": "bump_patch",
  "repo_version": "2026.04.08.1",
  "verify": true
}
```

3) Validate state (fast read-only readiness report):
```json
{ "managed_addon_id": "plugin.video.foo" }
```

### Autonomous iteration (agent loop)

Example tool call:
```json
{
  "managed_addon_id": "plugin.video.foo",
  "version_policy": "bump_patch",
  "repo_version": "2026.04.08.1",
  "verify": true
}
```

Success signal (only reliable): `verification.apply_verified == true`

Retry behavior:
- Retry only when `verification.can_retry == true`
- Sleep `verification.retry_delay_seconds` (if present)
- Stop when `verification.can_retry == false`
- Use `verification.retry_hint` as the operator-readable reason

Key `verification.apply_status` values:
- `applied` — version changed to target
- `already_current` — target already installed
- `repo_not_installed` — one-time repo install required
- `repo_not_ready` — repo refresh/metadata not ready
- `addon_not_found` — addon not visible in repo metadata
- `*_attempted_not_verified` — install/update requested but not confirmed
- `bridge_unreachable` — Kodi bridge not reachable
- `failed` — unknown failure (inspect output)

Operator rule: If the loop cannot complete, run `managed_addon_validate_state` and follow its output.

### Kodi-side manual step (required)
1) Install + enable **Kodi MCP Service**
2) Configure token:
   **Kodi → Add-ons → Services → Kodi MCP Service → Configure → Kodi MCP → MCP shared token**
3) Start the MCP server and wait briefly for **Developer status** to report ready
4) Then:
   **Developer → Developer setup**
5) Kodi opens **Install from zip file**
6) Manually browse to the staged `special://...` path shown and select the staged repo zip

Troubleshooting rule: **If anything fails, run `managed_addon_validate_state` first.**

---

## Optional HTTP endpoints (debug/compatibility)

When you run the FastAPI server (the same one used for remote MCP), these endpoints are also available:

- `GET /health`
- `GET /status`
- `/tools/*` (legacy HTTP endpoints used by `kodi-cli`)

The `/tools/*` endpoints are not the primary integration surface; they exist for debugging and backwards compatibility.

They are **not MCP** and are not used by MCP clients.

## Hosting example (systemd)

Example unit file (Linux). This hosts remote MCP at `http://<host>:8010/mcp`:

```ini
[Unit]
Description=Kodi MCP Server (FastAPI + Remote MCP)
After=network.target

[Service]
Type=simple
User=kodi
WorkingDirectory=/opt/kodi_mcp_server
Environment=KODI_JSONRPC_URL=http://kodi.local:8080/jsonrpc
Environment=KODI_BRIDGE_BASE_URL=http://kodi.local:8765
# Optional (protect /mcp)
Environment=MCP_API_KEY=change-me

ExecStart=/opt/kodi_mcp_server/.venv/bin/uvicorn kodi_mcp_server.main:app --host 0.0.0.0 --port 8010
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### Kodi bridge unreachable
Symptom: `apply_status = bridge_unreachable`; `managed_addon_validate_state` shows `reachable=false`.
Action:
- Start Kodi
- Ensure `service.kodi_mcp` is enabled
- Verify `KODI_BRIDGE_BASE_URL` and token match

### Repo not installed (first-time setup)
Symptom: `apply_status = repo_not_installed`; `dev_setup_available` may be true.
Action:
- Kodi → Add-ons → Services → Kodi MCP Service → Configure
- Developer → Developer setup
- Install from zip (select staged `special://` path)
If Developer setup is not ready yet, wait briefly for server auto-staging and re-check Developer status.

### Repo not ready / refresh lag
Symptom: `apply_status = repo_not_ready`.
Action:
- Wait a few seconds and retry
- Or manually run “Check for updates” in Kodi

### Addon not found in repo
Symptom: `apply_status = addon_not_found`.
Action:
- Retry once
- If still failing: confirm repo installed and repo zip staged correctly (`managed_addon_validate_state`)

### Install/update not verified
Symptom: `apply_status = install_attempted_not_verified` or `update_attempted_not_verified`.
Action:
- Retry (short delay)
- If persistent: verify repo enabled and check Kodi update settings (optionally trigger update manually)

### Unknown failure
Symptom: `apply_status = failed`.
Action:
- Run `managed_addon_validate_state`
- Inspect: artifacts, repo_ready_check, bridge state

## Testing

```bash
python -m pytest -v
```

## Next Steps

1. **Live integration testing** — Validate CLI + server against real remote Kodi
2. **Connection reuse** — Optimize transport with connection pooling
3. **README/API docs** — Expand documentation with examples
4. **Production validation** — Test across network conditions and Kodi states
