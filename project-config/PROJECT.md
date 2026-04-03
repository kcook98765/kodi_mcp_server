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

**Purpose:** Addon lifecycle management (build, publish, verify).

**Classification:** Requires human manual install between stages 2 and 4.

**Tools:**
- `build_addon_package` - Build addon ZIP (automated)
- `publish_addon_to_repo` - Publish ZIP to local repo (automated)
- `upload_bridge_addon_zip` - Serve ZIP from local repo (human-gated)
- `update_addon` - Check repo for updates (human-gated)
- `restart_bridge_addon` - Restart via JSON-RPC (hybrid)
- `verify_bridge_addon_deploy` - Verify version match (human-gated)

---

## Runtime vs Advisory vs Dev-Loop Separation

**Runtime surface:** Executes operations on addons that are already installed on remote Kodi. All side effects are constrained to existing installations.

**Advisory/diagnostic surface:** Purely read-only metadata queries. Can report state but cannot change anything.

**Dev-loop surface:** Manages addon lifecycle (build → publish → install → verify). Operates on build artifacts and repo server. Requires human manual install on remote Kodi.

**Key distinction:** Runtime and advisory tools cannot install addons. Dev-loop tools prepare deployment but require human action to actually install on Kodi.

---

## Dev-Loop Workflow (Stages 1-5)

```
Stage 1: build/package → build_addon_package (automated)
  ↓
Stage 2: publish to repo → publish_addon_to_repo (automated)
  ↓
[HUMAN MANUAL INSTALL CHECKPOINT]
  ↓
Stage 3: Kodi repo refresh → trigger_repo_refresh (human-gated)
  ↓
Stage 4: addon update/install → update_addon (human-gated)
  ↓
Stage 5: runtime verification → verify_bridge_addon_deploy (automated)
```

**Automated stages:** 1, 2, 5
**Hybrid stages:** None (restart_bridge_addon is hybrid but not in main flow)
**Human-gated stages:** 3, 4

### Stage Details

**Stage 1 - Build/Package:**
- Tool: `build_addon_package`
- Input: Source directory with `addon.xml`
- Output: `dist/addon_name-x.y.z.zip`
- Automation: Fully automated

**Stage 2 - Publish to Repo:**
- Tool: `publish_addon_to_repo`
- Input: Built ZIP, repo server running
- Output: ZIP at `http://localhost:8001/<addon_id>/addon.xml`
- Automation: Fully automated

**HUMAN MANUAL INSTALL CHECKPOINT:**
- Human must manually install ZIP on remote Kodi
- Go to Add-ons > My Add-ons > Install from zip file
- Select published ZIP, confirm installation

**Stage 3 - Kodi Repo Refresh:**
- Tool: `trigger_repo_refresh` (NOT YET IMPLEMENTED)
- Automation: Human-gated - no bridge endpoint exists
- Workaround: Manual refresh on remote Kodi

**Stage 4 - Addon Update/Install:**
- Tool: `update_addon`
- Automation: Human-gated - can detect but not force install
- Reports: Version mismatch, update available

**Stage 5 - Runtime Verification:**
- Tool: `verify_bridge_addon_deploy`
- Automation: Fully automated
- Output: `{addonid, current_version, expected_version, matches: boolean}`

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
- Examples: `build_addon_package`, `publish_addon_to_repo`, `verify_bridge_addon_deploy`

**Hybrid:** Optional human verification recommended. Server can complete but human should confirm.
- Examples: `restart_bridge_addon`, `ensure_addon_enabled`

**Human-Gated:** Requires human action on remote Kodi. Server reports state but cannot complete deployment.
- Examples: `update_addon`, `upload_bridge_addon_zip`, `trigger_repo_refresh`

### addon.xml Version Bump REQUIRED

**Mandatory before:**
1. Any `build_addon_package` operation
2. Any `publish_addon_to_repo` operation
3. Any commit/push tied to deployment
4. Any `update_addon` preparation

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
| Automated | build_addon_package, publish_addon_to_repo, verify_bridge_addon_deploy, runtime tools | No |
| Hybrid | restart_bridge_addon, ensure_addon_enabled | Optional (should verify) |
| Human-Gated | upload_bridge_addon_zip, update_addon, trigger_repo_refresh (if added), advisory tools | Required for actual deployment |

---

Last updated: 2026-03-31
