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
- `addon_execute`

These wrap bridge addon endpoints for basic Kodi GUI navigation and screenshot capture. Screenshot capture is remote-safe by default: the MCP server requests image data from the Kodi bridge, stores the PNG under the configured server screenshot store, serves it at `/screenshots/<id>.png`, and applies age/count cleanup using `KODI_SCREENSHOT_RETENTION_SECONDS` and `KODI_SCREENSHOT_MAX_FILES`.

Playback MCP tools:

- `kodi_player_active`
- `kodi_player_item`
- `kodi_player_seek`
- `kodi_player_pause`
- `kodi_player_stop`

These are curated JSON-RPC wrappers for autonomous agent playback tests. Agents should not call raw Kodi JSON-RPC, bridge HTTP fallbacks, or host-control scripts for active-player checks, seek/pause, or cleanup. If a playback workflow needs another Kodi operation, add it as an explicit MCP tool.

Vision-analysis tools are intentionally not exposed unless a future vision model integration is explicitly configured with `KODI_VISION_MODEL_URL` and `KODI_VISION_MODEL_NAME`; without that config, only screenshot capture is offered.
The bridge endpoints and remote MCP wrappers are live-smoked; the running system
`kodi-mcp.service` reports the MCP tool list including these GUI and playback tools.

### Managed Addon Loop

Preferred agent loop:

1. `managed_addon_register`
2. `managed_addon_build_publish_stage_and_apply`
3. `managed_addon_validate_state` when troubleshooting or when apply verification is incomplete

Preferred split-host artifact loop:

1. `artifact_upload_zip`
2. `repo_publish_stage_apply_artifact`
3. `kodi_gui_screenshot`, `kodi_player_*`, or addon-specific verification

Artifact upload validates addon zips before they reach the repo. The one-shot artifact workflow returns `apply_verified`, `installed_version_after`, `apply_status`, `can_retry`, and `failure_reason` so agents do not need to infer success from a raw apply attempt.

Hard success signal:

- `verification.apply_verified == true`

Retry only when:

- `verification.can_retry == true`

The first install path for a brand-new addon may still require a Kodi-side UI step: **Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**. After initial installation, repeated updates are handled by the managed apply workflow when Kodi, the bridge, and repo metadata are healthy.

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
- Bridge `/mcp/state`: ok, with registration, staged repo archive, `dev_setup_available=true`, and an install hint that points to Install from repository.
- Managed-addon smoke using `script.kodi_mcp_test`: package/upload/publish/stage succeeded, initial UI install was completed through Kodi, and post-initial managed apply updated the addon to `0.0.9`.
- Attempted the bridge `InstallAddon(script.kodi_mcp_test)` builtin before first install; Kodi accepted the request but the addon remained uninstalled after polling, so first install still requires Kodi UI.
- Rebuilt/reinstalled `service.kodi_mcp` after fixing its `mcp_token` setting metadata; bridge health and capabilities remained ok.
- Added bridge GUI action/screenshot support and server MCP wrappers.
- Live bridge smoke:
  - `/gui/action` with `down` and `back`: ok
  - `/gui/screenshot`: ok, returned a non-empty PNG under addon profile screenshots
- The restarted MCP service reports the curated MCP tool list and remote MCP smoke passes.

## Bridge Addon Ownership

The server repo does not own `service.kodi_mcp` source. The standalone `kodi_mcp_addon` repo owns Kodi-resident addon code and packaging. The server repo owns host-side clients, MCP tools, bridge contract tests, and integration docs.

`scripts/build_service_addon.py` delegates to the standalone addon repo build script. Set `KODI_MCP_ADDON_REPO` to override the default addon repo path.

## Future TODO

- Keep `README.md`, this file, and `project-config/REPO_WORKFLOW_RUNBOOK.md` aligned whenever managed-addon behavior changes.
- Add focused tests around `.env` loading and startup config behavior when touching configuration code.
- Keep Milestone A bridge contract tests aligned with `service.kodi_mcp` endpoint changes.
- Keep local-only files out of Git before any GitHub push; verify with `git status --short --ignored`.
- Revisit direct container-to-host MCP TCP only after the host-control workflow remains stable.
- Push prepared branches only after explicit user approval.
