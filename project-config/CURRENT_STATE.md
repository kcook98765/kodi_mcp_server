# kodi_mcp_server - Current State

**Last updated:** 2026-03-31

## Completed Tasks (Phase 7: CLI Wrapper)

**Date:** 2026-03-31

### Summary
Implemented thin CLI wrapper `kodi-cli` that provides deterministic, machine-friendly interface to the backend server.

### Command Structure (Hierarchical)
- `kodi-cli system status` — Get server/system status
- `kodi-cli jsonrpc call --method <name>` — Execute JSON-RPC command
- `kodi-cli addon info --addonid <id>` — Get bridge addon info
- `kodi-cli addon execute --addonid <id>` — Execute addon via bridge
- `kodi-cli builtin exec --command <cmd>` — Execute Kodi builtin
- `kodi-cli log tail --lines <n>` — Get log tail from bridge
- `kodi-cli service status --addonid <id>` — Get service metadata

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

---

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

## Current dev loop (managed-addon workflow)

### Source of truth
- Addon source lives in a **local folder** (outside the dev repo).
- The MCP server registers it via:
  - `managed_addon_register(source_path)`

### Core workflow
1) `managed_addon_register` (one-time per addon source path)
2) `managed_addon_build_publish_and_stage` (repeat per edit cycle)
3) `managed_addon_validate_state` (fast read-only verification/troubleshooting)

### Autonomous loop behavior
- `managed_addon_build_publish_stage_and_apply` is the primary tool for repeated agent-driven iterations after one-time repo installation.
- `verification.apply_verified == true` is the only hard success signal.
- `verification.can_retry`, `verification.retry_hint`, and `verification.retry_delay_seconds` guide bounded retries.
- Retry behavior is deterministic and conservative (prefer “not verified” over assuming success).
- First-time repo installation in Kodi remains a manual checkpoint.

### Dev repo model
- `repo/dev-repo` is **artifact-only**:
  - `zips/`
  - `addons.xml`
  - `addons.xml.md5`
- The repo zip is built from `repo/dev-repo/` and staged to Kodi via the bridge.

### Kodi install step (explicit, user-guided)
- User runs **Developer setup** in the Kodi addon:
  - Kodi → Add-ons → Services → Kodi MCP Service → Configure
  - Developer → Developer setup
- Kodi opens **Install from zip file**.
- User manually selects the staged repo zip from the shown `special://...` path.

### Validation model
- `managed_addon_validate_state` is the primary troubleshooting tool.
  - Includes bridge reachability and `dev_setup_available`.

Note: Older dev-loop tools (`build_addon_package`, `publish_addon_to_repo`, `update_addon`, `upload_bridge_addon_zip`, `verify_bridge_addon_deploy`, etc.) are now considered **legacy** and are not the primary workflow.

---

## Git Commit/Push Rules

### When Worker Must Commit/Push

**Worker commits/pushes when:**
1. Server code changes (new tools, bug fixes, refactoring)
2. Dev-loop workflow changes (new stages, new tools)
3. ANY change to implementation files in `project/src/`

**Human approval required before push:**
- All commits require explicit human approval
- Push only when change is complete and tested
- Document changes in commit message

**DO NOT commit/push when:**
- Just reading or analyzing code (no changes)
- Testing locally without persistence
- Documentation changes without implementation changes

### addon.xml Version Bump Rules

**Mandatory version bump before:**
1. Any `build_addon_package` operation
2. Any `publish_addon_to_repo` operation
3. Any commit/push tied to deployment
4. Any `update_addon` preparation

**Version bump process:**
1. Edit `addon.xml` `version` attribute
2. Update `CHANGELOG.md` or release notes
3. Commit both files together
4. Push with message: "Bump version to X.Y.Z for deployment"

**Rule:** If you're preparing deployment, bump version first. If you're just fixing bugs, bump version after fixing, before build/publish.

### Manual Install Checkpoint

**STOP and require human action when:**
1. Stage 2 (publish to repo) completes successfully
2. Dev-loop tool reports "update available" but not installed
3. Server reports version mismatch (expected vs installed)
4. `trigger_repo_refresh` fails (no bridge endpoint)

**Human checkpoint message:**
```
Dev-loop stage 2 complete. ZIP published to repo.

NEXT STEP (HUMAN):
1. On remote Kodi, go to Add-ons > My Add-ons > Install from zip file
2. Select the published ZIP from repo
3. Confirm installation
4. Return to server to verify with `verify_bridge_addon_deploy`
```

### Worker Handoff Rules

**Handoff from TOP to worker:**
- Clear task scope (which surface? which tool?)
- Explicit success criteria
- Document any human-gated steps
- Define rollback if needed

**Handoff from worker to TOP:**
- Task complete with success message
- Any human-gated steps identified
- Git commit/push request with approval prompt
- Any version bump actions taken

**Example handoff:**
```
TOP: "Worker, build and publish addon v1.2.0 to repo. Human will install manually."
Worker: "Build complete, published to repo. Waiting for human install."
Worker: "Human install confirmed. Verifying version match..."
Worker: "Version match verified. Git commit pending approval."
TOP: "Approved. Push to main branch."
```

---

## Gaps and Known Issues

### Missing Bridge Endpoints

1. **trigger_repo_refresh** - No bridge endpoint for auto-refresh
   - Severity: HIGH
   - Workaround: Document manual process

2. **auto_install** - No bridge endpoint for forced install
   - Severity: MEDIUM
   - Workaround: Human manual install

### Tools Removed/Deprecated

- `tools/verify_bridge_addon_deploy` — Removed from runtime surface, moved to dev-loop (human-gated)
- `tools/upload_bridge_addon_zip` — Removed from runtime surface, moved to dev-loop (human-gated)
- `tools/update_addon` — Removed from runtime surface, moved to dev-loop (human-gated)
- `tools/wait_for_addon_version` — Removed (assumes version enforcement not possible)

---

## Next Steps

1. **Implement dev-loop namespace** — Expose `/dev-loop/*` endpoints
2. **Document bridge API** — Create `bridge_api_spec.md` with endpoint schemas
3. **Add trigger_repo_refresh** — If JSON-RPC supports it
4. **Update CLI wrapper** — Add namespace prefixes (`/runtime/`, `/diagnostic/`, `/dev-loop/`)
5. **Write integration tests** — Test transport layers with mocks

---

## CURRENT_STATE.md Maintenance

**This file must be updated after every meaningful change.**  
If you modify code, config, or architecture, update this file before finishing.  
If no changes were made, confirm CURRENT_STATE.md is still accurate.

---

Last updated: 2026-03-31
