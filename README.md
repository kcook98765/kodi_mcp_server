# Kodi MCP Server

This repository provides an **MCP (Model Context Protocol) server for Kodi**.

It exposes a curated set of Kodi operations (Kodi JSON-RPC + the Kodi MCP bridge addon) as MCP tools, so agent clients (like VS Code/Cline) can control and inspect Kodi in a structured way.

## Quick start

> For the canonical repo publish/install/update behavior, see:
> **project-config/REPO_WORKFLOW_RUNBOOK.md**
>
> For the current local handoff state and next TODOs, see:
> **project-config/CURRENT_STATE.md**

**First-time onboarding (repo install)**
1) Install + enable the Kodi bridge addon: `service.kodi_mcp`
2) Set the shared token in Kodi addon settings (**service.kodi_mcp → Kodi MCP → MCP shared token**)
3) Start this server
4) Install the Kodi MCP repository add-on once if it is not already installed: `repository.kodi-mcp`
5) For a brand-new target addon, use Kodi UI: **Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**

**Managed addon loop (after repo is installed in Kodi)**
1) Register local addon: `managed_addon_register`
2) Build/publish/stage/apply: `managed_addon_build_publish_stage_and_apply`
3) If needed: `managed_addon_validate_state`

Success = `verification.apply_verified == true`
Retry only if `verification.can_retry == true`

## Kodi addon requirement

- This MCP server requires the Kodi bridge addon to be installed and enabled in Kodi:
  https://github.com/kcook98765/kodi_mcp_addon
- The addon exposes the HTTP bridge used by this server, including GUI action/screenshot helpers for guided first-install checks.
- After the addon token is configured and this server starts, the server can register with the bridge and stage repository content for the managed update loop.
- Without the addon, the MCP server cannot talk to Kodi.
- Kodi-resident bridge addon source is owned by the standalone `kodi_mcp_addon` repo, not this server repo.

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
- `REPO_BASE_URL` for repo and screenshot URLs visible to Kodi/clients on other hosts
- `KODI_SCREENSHOT_STORE_DIR`, `KODI_SCREENSHOT_RETENTION_SECONDS`, `KODI_SCREENSHOT_MAX_FILES`
- `KODI_VISION_MODEL_URL`, `KODI_VISION_MODEL_NAME`; when unset, screenshot capture remains available but vision-analysis tools are not exposed

Local development can use a repo-root `.env` file copied from `.env.example`.
Process environment values take precedence over `.env` values. Keep `.env`,
`.env.*`, local backups, keys, and logs out of Git.

## First connection test (stdio or remote)

Once connected, try these MCP tools first:
1. `kodi_status`
2. `bridge_health`
3. `bridge_runtime_info`

GUI helpers:
- `kodi_gui_action` sends basic navigation actions (`up`, `down`, `left`, `right`, `select`, `back`, `home`, `context`, `info`).
- `kodi_gui_screenshot` captures a Kodi GUI screenshot through the bridge addon, stores it on the MCP server by default, and returns a `/screenshots/<id>.png` URL.
- These can assist first-install UI navigation, but deterministic bridge/repo state checks should remain the primary workflow.

If you’re testing the **remote** transport directly, you can also do a minimal curl initialize:

```bash
curl -i -N http://<server-host>:8010/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-mcp-api-key: <optional>" \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## Managed addon development workflow (golden path)

### Repo workflow (publish/install/update)

The repo system has a known rule: **brand-new addons require a one-time manual install in Kodi UI**, and updates can be automated after that.

See the runbook:
- **project-config/REPO_WORKFLOW_RUNBOOK.md**

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
3) Install + enable **Kodi MCP Repository** (`repository.kodi-mcp`) once if it is missing
4) For each brand-new target addon:
   **Kodi → Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**
5) Rerun the managed addon apply/update workflow after the first install

Note: a staged `dev-repo.zip` is repository content used by the server/bridge refresh loop; it is not itself an installable Kodi add-on zip.

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
- Install `repository.kodi-mcp` once.
- Then use **Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**.
- Do not try to install the staged `dev-repo.zip`; it is repository content, not an installable add-on zip.

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
