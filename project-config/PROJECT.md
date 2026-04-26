# kodi_mcp_server - Project Documentation

## Overview

Custom Python middle-layer server for remote Kodi integration via three-surface design.

## Three-Surface Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ kodi_mcp_server (localhost:8000)                                │
│                                                                 │
│  /runtime/*     ← Runtime surface (execute, status, logs)       │
│  /diagnostic/*  ← Advisory surface (read-only metadata)         │
│  /dev-loop/*    ← Dev-loop surface (build, publish, verify)    │
└─────────────────────────────────────────────────────────────────┘
```

### Runtime Surface (`/runtime/*`)

**Purpose:** Core operations on already-installed addons.

**Classification:** All tools assume addon already present on remote Kodi.

**Tools:**
- `execute_bridge_addon` - Execute addon via bridge
- `execute_addon` - Execute addon via JSON-RPC
- `ensure_bridge_addon_enabled` - Enable addon (conditional)
- `ensure_addon_enabled` - Enable addon via JSON-RPC
- `get_bridge_log_tail` - Read logs
- `write_bridge_log_marker` - Write trace marker
- `execute_bridge_builtin` - Execute Kodi builtin
- `get_addons` - List all addons
- `get_addon_details` - Get addon metadata
- `list_addons` - Filtered addon listing
- `service_status` - Service addon metadata

**Constraint:** Runtime tools operate ONLY on installed addons. Cannot install if addon missing.

### Advisory/Diagnostic Surface (`/diagnostic/*`)

**Purpose:** Read-only metadata queries. No side effects.

**Classification:** Advisory-only - can report state but cannot change it.

**Tools:**
- `get_bridge_version` - Read bridge version (cannot enforce)
- `get_bridge_runtime_info` - Runtime metadata
- `get_bridge_file` - Read file from Kodi
- `get_bridge_addon_info` - Addon metadata via bridge
- `get_bridge_log_markers` - Log markers
- `get_bridge_control_capabilities` - Capabilities list
- `check_bridge_addon_version` - Check version (cannot enforce)
- `is_addon_installed` - Read-only check
- `is_addon_enabled` - Read-only check
- `run_addon_and_report` - Execute and report events

### Dev-Loop Surface (`/dev-loop/*`)

**Purpose:** Developer workflow for building/publishing addon artifacts and staging a repo zip to Kodi.

**Source model:** Local addon source trees live in folders outside the dev repo; they are registered and managed by the MCP server.

**Primary tools (managed-addon workflow):**
- `managed_addon_register`
- `managed_addon_list`
- `managed_addon_get`
- `managed_addon_build_publish_and_stage`
- `managed_addon_validate_state`

**Dev repo role:** `repo/dev-repo` is artifact-only (`zips/`, `addons.xml`, `addons.xml.md5`). A repo zip is built from this directory and staged to Kodi via the bridge.

**Kodi-side handoff:** The Kodi bridge addon stores registration/staging state and lets agents inspect GUI state. The staged `dev-repo.zip` is repository content for refresh/state handoff, not an installable add-on zip. Brand-new target addons are first installed through **Add-ons → Install from repository → Kodi MCP Repository → target addon → Install** after `repository.kodi-mcp` is installed once.

Note: older dev-loop tools (`build_addon_package`, `publish_addon_to_repo`, `update_addon`, `upload_bridge_addon_zip`, `verify_bridge_addon_deploy`, etc.) are now considered **legacy** and are not the primary recommended path.

---

## Runtime vs Advisory vs Dev-Loop Separation

**Runtime surface:** Executes operations on addons that are already installed on remote Kodi. All side effects are constrained to existing installations.

Playback runtime tools are MCP-first and intentionally curated:

- `kodi_player_active`
- `kodi_player_item`
- `kodi_player_seek`
- `kodi_player_pause`
- `kodi_player_stop`

Autonomous agents should use these tools for playback verification and cleanup. They should not fall back to raw Kodi JSON-RPC, bridge HTTP endpoints, host-control scripts, or ad hoc curl unless the MCP surface is being debugged by a human.

**Advisory/diagnostic surface:** Purely read-only metadata queries. Can report state but cannot change anything.

**Dev-loop surface:** Manages addon lifecycle (build → publish → install → verify). Operates on build artifacts and repo server. Requires human manual install on remote Kodi.

**Key distinction:** Runtime and advisory tools cannot install addons. Dev-loop tools prepare deployment but require human action to actually install on Kodi.

---

## Dev-Loop Workflow (managed-addon)

```
Stage 1: register local source path → managed_addon_register
  ↓
Stage 2: build + publish + stage repo zip → managed_addon_build_publish_and_stage
  ↓
[HUMAN MANUAL INSTALL CHECKPOINT]
  ↓
Stage 3: user/agent uses Kodi UI → Install from repository → Kodi MCP Repository → target addon → Install
  ↓
Stage 4: validate readiness/troubleshoot → managed_addon_validate_state
```

**Key principle:** the server can stage repository content and automate updates after first install, but the first install of a brand-new target addon remains Kodi UI guided.

---

## Critical Rules

### Repo State ≠ Installed Addon State

**Repo may have newer version, but installed addon only changes via human manual install on remote Kodi.**

- Repo state = what's available in local repo server
- Installed state = what's actually installed on remote Kodi
- These are never automatically synchronized

### Runtime Tools Operate Only on Installed Addon

**All runtime tools assume addon is already present on remote Kodi.**

- Cannot install missing addons via runtime tools
- Cannot enforce version changes via runtime tools
- Can only operate on what exists

### Dev-Loop Tools Operate on Repo/Build Artifacts

**Dev-loop tools prepare deployment but require human action.**

- Build tools work on local source
- Publish tools work on local repo server
- Verification tools compare installed vs expected
- Actual installation requires human manual action

### Automated vs Hybrid vs Human-Gated Distinctions

**Automated:** No human action required. Server completes operation fully.
- Examples: `managed_addon_build_publish_and_stage`

**Hybrid:** Optional human verification recommended. Server can complete but human should confirm.
- Examples: `restart_bridge_addon`, `ensure_addon_enabled`

**Human-Gated:** Requires human action on remote Kodi. Server reports state but cannot complete deployment.
- Examples: Kodi-side Add-ons → Install from repository → Kodi MCP Repository → target addon → Install

### addon.xml Version Bump REQUIRED

**Mandatory before:**
1. Any `managed_addon_build_publish_and_stage` operation
2. Any commit/push tied to deployment
3. Any release/update preparation

**Version bump process:**
1. Edit `addon.xml` `version` attribute
2. Update `CHANGELOG.md` or release notes
3. Commit both files together
4. Push with message: "Bump version to X.Y.Z for deployment"

### No Automatic Kodi Deployment

**This server does NOT automatically deploy to Kodi.**

- Build/publish is local (workspace + local repo)
- Actual Kodi installation requires manual action
- Verification is advisory (reports mismatch)
- Enforcement requires human intervention

---

## Tool Classification Summary

| Classification | Tools | Human Action Required |
|----------------|-------|----------------------|
| Automated | managed_addon_build_publish_and_stage, runtime tools | No |
| Hybrid | restart_bridge_addon, ensure_addon_enabled | Optional (should verify) |
| Human-Gated | Kodi Install from repository first-install flow, advisory tools | Required for first install |

---

Last updated: 2026-03-31
