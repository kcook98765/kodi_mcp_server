# Kodi MCP Repo Workflow Runbook (agent-safe)

This runbook documents the **current, proven behavior** of the Kodi MCP repo install/update system.

Goal: enable agents (and operators) to publish and update addons **without relying on server-host filesystem paths**.

---

## Terminology

- **MCP Server**: `kodi_mcp_server` (FastAPI + MCP).
- **Bridge addon**: `service.kodi_mcp` (Kodi-resident HTTP bridge).
- **Repo addon**: `repository.kodi-mcp` (installed in Kodi once; points to the server repo URL).

Bridge addon source is maintained in the standalone `kodi_mcp_addon` repo. This server repo owns the host-side MCP/API implementation and bridge contract tests.

---

## Bridge-absent first install

A Kodi target with healthy stock JSON-RPC but no `service.kodi_mcp` must begin
with `bridge_bootstrap_status`. Kodi 19–22 expose no stock JSON-RPC operation to
install an arbitrary ZIP, add a source/repository, or execute the internal
`InstallAddon`/`InstallFromZip` built-ins.

The supported contract is therefore user-assisted and fail-closed:

1. The server exposes one pinned, validated bridge bundle at
   `/bootstrap/manifest.json` and `/bootstrap/service.kodi_mcp.zip`.
2. `bridge_bootstrap_status` returns `user_action_required` while the addon is
   absent. It does not mutate Kodi settings or claim install success.
3. The user reviews Kodi's Unknown Sources warning, installs that exact ZIP
   through Kodi UI, and configures the shared token.
4. Re-run the tool. Success requires `state=already_installed`, `verified=true`,
   and `next_stage=managed_deployment`; version, source Git SHA, and source
   fingerprint must all match.
5. A stale/wrong same-ID addon returns `next_stage=authoritative_update` and
   converges into the existing update/normalization flow.

Release preparation must provide the official ZIP and matching manifest. In a
source checkout, `scripts/prepare_bridge_bootstrap.py` prepares both without
contacting Kodi. Prefer HTTPS for `REPO_BASE_URL`. Do not remotely enable Unknown
Sources, inject files, or treat a matching addon ID alone as success.

---

## Rule: first install is manual for brand-new managed addons

If an addon has **never been installed** in Kodi before, then after it is published into the Kodi MCP repo:

- The user must do a **one-time manual install** in Kodi UI:
  - **Add-ons → Install from repository → Kodi MCP Repository → (select addon) → Install**

After that first install, future updates for that addon can be automated.

---

## Agent-safe publish flow (remote)

### Step 1 — Upload artifact (zip) to server-owned artifact store

`POST /tools/artifacts/upload` (multipart)

- `file`: the addon zip
- `addon_id` (optional)
- `version` (optional)

Returns an `artifact_id`.

### Step 2 — Publish artifact into the repo

`POST /tools/repo/publish_artifact` (JSON)

- `artifact_id`
- `addon_id`
- `addon_name`
- `addon_version`
- `provider_name`

Returns repo-relative `zip_url` and status/action fields.

---

## Automatable update flow (only after first install)

### Step 3 — Apply update in Kodi

`POST /tools/update_addon` (JSON)

- `addonid`
- `timeout_seconds` (optional)
- `poll_interval_seconds` (optional)

Behavior:
- triggers repo refresh (`UpdateAddonRepos`)
- triggers install/update (`InstallAddon`)
- waits until the installed addon version matches the repo version

### Handling the manual-first-install case

If `POST /tools/update_addon` returns:

- `requires_initial_user_install: true`

Then perform the one-time UI install and retry future updates via `POST /tools/update_addon`.

The one-time UI path is:

**Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**

Do not install the staged `dev-repo.zip` directly. That archive is repository
content used by the server/bridge refresh loop, not an installable Kodi add-on
zip.

---

## Notes on endpoint classes

- **Preferred (agent-safe)**: `/tools/artifacts/upload`, `/tools/repo/publish_artifact`, `/tools/update_addon`.
- **Admin/internal helpers** (may require server-local paths): e.g. path-based publish endpoints.

Agents should avoid relying on internal filesystem paths; success payloads are designed to use ids + repo URLs.

---

## Optional capability: WebSocket notifications

Kodi WebSocket notifications (typically `ws://<kodi-host>:9090/jsonrpc`) are an **optional advanced capability**.

- Core repo publishing + update workflows (artifact upload → publish → update) do **not** require WebSocket.
- Compatibility smoke tests should treat notification sampling failures as **non-blocking / advisory**.

---

## Current local agent integration note

In the current Kodi agent stack, direct container-to-host MCP TCP connectivity is
not the reliable control path. Use the Kodi agent host-control workflow proxy for
live local smoke tests, and use this repo's MCP/HTTP tests for server-level
regression checks.

Before any GitHub push:

- confirm `.env`, `.env.*`, and local backup files are untracked/ignored
- run the server test suite
- verify only intentional files appear in `git status --short`
