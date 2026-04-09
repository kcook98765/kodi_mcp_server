# Kodi MCP Repo Workflow Runbook (agent-safe)

This runbook documents the **current, proven behavior** of the Kodi MCP repo install/update system.

Goal: enable agents (and operators) to publish and update addons **without relying on server-host filesystem paths**.

---

## Terminology

- **MCP Server**: `kodi_mcp_server` (FastAPI + MCP).
- **Bridge addon**: `service.kodi_mcp` (Kodi-resident HTTP bridge).
- **Repo addon**: `repository.kodi-mcp` (installed in Kodi once; points to the server repo URL).

---

## Rule: first install is manual for brand-new addons

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

---

## Notes on endpoint classes

- **Preferred (agent-safe)**: `/tools/artifacts/upload`, `/tools/repo/publish_artifact`, `/tools/update_addon`.
- **Admin/internal helpers** (may require server-local paths): e.g. path-based publish endpoints.

Agents should avoid relying on internal filesystem paths; success payloads are designed to use ids + repo URLs.
