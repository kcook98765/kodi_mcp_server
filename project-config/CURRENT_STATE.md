# kodi_mcp_server - Current State

**Last updated:** 2026-04-26

## Summary

`kodi_mcp_server` is the host-side MCP/FastAPI server for Kodi automation. It exposes:

- stdio MCP entrypoint: `kodi-mcp`
- remote Streamable HTTP MCP endpoint: `/mcp`
- FastAPI debug/compatibility endpoints such as `/health`, `/status`, and `/tools/*`
- managed-addon workflow tools for building, publishing, staging, applying, and validating Kodi addon iterations

The companion Kodi bridge addon (`service.kodi_mcp`) must be installed, enabled, and configured with the same shared token used by this server.

## Current Local Stack Notes

- Repo path: `/home/kyle/kodi_mcp_server`
- Git remote: `git@github.com:kcook98765/kodi_mcp_server.git`
- Work branch for this review: `review/kodi-mcp-server-hygiene-20260425`
- Bridge addon source of truth: `/srv/openclaw-projects/kodi_mcp_addon/workspace/project`
- Local env backups such as `.env.bak.localhost-20260424` are local-only and must not be committed.
- The active Kodi agent stack uses host-control workflow proxying because direct container-to-host MCP TCP remains blocked/timeouts in the current environment.
- `kodi-local.service`, `kodi-mcp.service`, `openclaw-kodi-agent.service`, and `kodi-agent-host-control.service` were previously confirmed active/enabled in the Kodi agent handoff.

## Configuration

The server loads simple `KEY=VALUE` pairs from these files when present:

1. repo-root `.env`
2. legacy `project/.env`

Existing process environment values take precedence over local `.env` values. The repo-root `.env` file is the documented local development default.

Committed examples:

- `.env.example` documents operator configuration.
- `.env.test` is a non-secret test template.

Ignored local files include `.env`, `.env.*`, local backups, logs, caches, build output, virtualenvs, and egg metadata.

## Primary Workflows

### MCP Client

- Stdio command: `kodi-mcp`
- Remote endpoint: `http://<host>:8010/mcp`
- Optional remote API key header: `x-mcp-api-key`

First MCP checks:

1. `kodi_status`
2. `bridge_health`
3. `bridge_runtime_info`

GUI MCP tools:

- `kodi_gui_action`
- `kodi_gui_screenshot`

These wrap bridge addon endpoints for basic Kodi GUI navigation and screenshot capture.
The bridge endpoints are live-smoked, but the running system `kodi-mcp.service`
must be restarted with sudo before remote MCP clients see these new tools.

### Managed Addon Loop

Preferred agent loop:

1. `managed_addon_register`
2. `managed_addon_build_publish_stage_and_apply`
3. `managed_addon_validate_state` when troubleshooting or when apply verification is incomplete

Hard success signal:

- `verification.apply_verified == true`

Retry only when:

- `verification.can_retry == true`

The first install path for a brand-new addon may still require a Kodi-side UI step through the bridge addon's Developer setup flow. After initial installation, repeated updates are handled by the managed apply workflow when Kodi, the bridge, and repo metadata are healthy.

## Public Entry Points

Python package console scripts from `pyproject.toml`:

- `kodi-mcp-server` -> `kodi_mcp_server.main:main`
- `kodi-mcp` -> `kodi_mcp_mcp.server:main`
- `kodi-mcp-wrapper` -> `kodi_mcp_mcp.server:main`

Server composition:

- `kodi_mcp_server.main` creates the FastAPI app, mounts repo/debug routes, and mounts remote MCP at `/mcp`.
- `kodi_mcp_mcp.server` starts the stdio MCP server.
- `kodi_mcp_mcp.server_core` defines the shared MCP tools and dispatch behavior.

## Test And Verification Commands

From `/home/kyle/kodi_mcp_server`:

```bash
.venv/bin/pytest
python3 -m py_compile src/kodi_mcp_server/config.py src/kodi_mcp_server/main.py src/kodi_mcp_mcp/server.py src/kodi_mcp_mcp/server_core.py
.venv/bin/python -c "from kodi_mcp_server.main import app; from kodi_mcp_mcp.server import main as stdio_main; print(app.title); print(callable(stdio_main))"
```

For live Kodi validation, prefer the Kodi agent stack's host-control workflow checks from `/srv/openclaw-stacks/kodi-agent`, because that path matches the current local environment.

## Latest Live Smoke

After installing the updated standalone bridge addon package into local Kodi and restarting Kodi:

- Kodi JSON-RPC health: ok
- Bridge health: ok
- Bridge `/status`: `service.kodi_mcp` `0.2.16`
- Bridge `/capabilities` and `/control/capabilities`: ok
- Bridge `/mcp/state`: ok, with registration, staged repo zip, `dev_setup_available=true`, and install hint
- Managed-addon smoke using `script.kodi_mcp_test` reached the expected first-install gate: package/upload/publish/stage succeeded, apply reported `requires_initial_user_install=true` because the test addon is not installed yet.
- Attempted the bridge `InstallAddon(script.kodi_mcp_test)` builtin; Kodi accepted the request but the addon remained uninstalled after polling, so the Kodi UI first install is still required.
- Rebuilt/reinstalled `service.kodi_mcp` after fixing its `mcp_token` setting metadata; bridge health and capabilities remained ok.
- Added bridge GUI action/screenshot support and server MCP wrappers.
- Live bridge smoke:
  - `/gui/action` with `down` and `back`: ok
  - `/gui/screenshot`: ok, returned a non-empty PNG under addon profile screenshots
- The currently running MCP service still reported the old 21-tool list because `sudo systemctl restart kodi-mcp.service` requires interactive sudo.

## Bridge Addon Ownership

The server repo does not own `service.kodi_mcp` source. The standalone `kodi_mcp_addon` repo owns Kodi-resident addon code and packaging. The server repo owns host-side clients, MCP tools, bridge contract tests, and integration docs.

`scripts/build_service_addon.py` delegates to the standalone addon repo build script. Set `KODI_MCP_ADDON_REPO` to override the default addon repo path.

## Future TODO

- Keep `README.md`, this file, and `project-config/REPO_WORKFLOW_RUNBOOK.md` aligned whenever managed-addon behavior changes.
- Add focused tests around `.env` loading and startup config behavior when touching configuration code.
- Keep Milestone A bridge contract tests aligned with `service.kodi_mcp` endpoint changes.
- Complete the Kodi UI first install for `script.kodi_mcp_test`, then rerun managed apply to verify post-install update automation.
- Keep local-only files out of Git before any GitHub push; verify with `git status --short --ignored`.
- Revisit direct container-to-host MCP TCP only after the host-control workflow remains stable.
- Push prepared branches only after explicit user approval.
