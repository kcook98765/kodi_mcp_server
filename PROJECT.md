# PROJECT.md - kodi_mcp_server

## Overview

**kodi_mcp_server** is a custom Python middle-layer server for remote Kodi integration.

This is **not** built-in OpenClaw MCP support. This is a custom server built specifically to sit between:

1. **Local CLI wrappers** (future) — thin commands the agent invokes
2. **Remote Kodi addon / bridge endpoints** — HTTP API for Kodi control
3. **Kodi JSON-RPC** — native Kodi protocol over HTTP
4. **Kodi repo server** — serving addon packages locally

### Three-Surface Design

The server exposes three distinct operational surfaces:

- **Runtime surface** — Core operations on already-installed addons (execute, status, logs, enable/disable)
- **Advisory/diagnostic surface** — Read-only metadata queries (version checks, file reads, capabilities)
- **Dev-loop surface** — Addon lifecycle management (build, publish, verify)

**Critical constraint:** Runtime surface operates ONLY on installed addons. Dev-loop surface operates on build artifacts and repo server. Repo state ≠ Installed addon state. Human manual install required between dev-loop stages 2 and 4.

## Repository Structure

```
project/                    # Canonical git-controlled codebase
├── src/kodi_mcp_server/   # Main server implementation
│   ├── main.py            # Entry point (uvicorn server)
│   ├── app_shared.py      # Shared app creation
│   ├── mcp_app.py         # MCP-style endpoint handler
│   ├── repo_app.py        # Repo server endpoints
│   ├── config.py          # Configuration loading
│   ├── models/            # Request/response models
│   ├── transport/         # Transport layers
│   └── tools/             # Tool implementations
├── scripts/               # Helper scripts
│   ├── publish_repository_addon.py
│   ├── build_service_addon.py
│   ├── repo_server.py
│   └── ...
└── README.md              # Project documentation
```

**Workspace root** (`/home/node/.openclaw/workspace`) contains:
- OpenClaw guidance files (`SOUL.md`, `AGENTS.md`, etc.)
- Memory files (`memory/`, `MEMORY.md`)
- **NOT** implementation code

## Architecture

```
┌──────────┐
│  agent   │
└────┬─────┘
     │ (future CLI wrappers)
     ↓
┌──────────────────────┐
│  local CLI commands  │
└────┬─────────────────┘
     │ (HTTP requests)
     ↓
┌──────────────────────┐
│ kodi_mcp_server      │
│ (this project)       │
└────┬─────────────────┘
     │
     ├→ HTTP bridge client → remote Kodi addon
     ├→ HTTP JSON-RPC client → Kodi JSON-RPC
     └→ Repo server → local addon packages
```

## Current Status

**In progress** — backend server with finalized three-surface design.

### What Exists

- HTTP server with `uvicorn`
- Three-surface design: runtime, advisory/diagnostic, dev-loop
- Transport layers: `HttpJsonRpcTransport`, `HttpBridgeClient`
- Message models: `RequestMessage`, `ResponseMessage`
- Config loading from environment variables
- Dev-loop workflow: 5-stage process (build, publish, refresh, update, verify)
- Helper scripts for repo/addon operations

### What's Needed

- Implement dev-loop namespace endpoints (`/dev-loop/*`)
- Document human-gated workflow clearly
- Add `trigger_repo_refresh` tool if JSON-RPC supports it
- Rename `upload_bridge_addon_zip` for clarity (or document purpose)
- Write integration tests for transport layers
- Create `bridge_api_spec.md` documenting bridge endpoint schemas
- Update CLI wrapper to expose three-surface namespaces

## Goals

1. **Stable backend** — endpoints that work reliably
2. **Structured outputs** — predictable JSON responses
3. **Clear contract** — documented interface for CLI wrappers
4. **Safe evolution** — incremental changes with rollback paths

## Notes

- **Remote-only Kodi validation** — Kodi is accessed remotely via HTTP, never locally
- **No direct addon development** — this server integrates with existing Kodi addons, doesn't build them
- **Future consumption** — another OpenClaw instance may use wrapper commands backed by this server
- **Implementation code only in `project/`** — workspace root is for OpenClaw config and memory

### Server-Only Dev-Loop Model (Frozen)

**kodi_mcp_server packages an internal test addon for validation only. It does NOT package the real Kodi addon (kodi_mcp_addon).**

**Authoritative paths for server dev-loop:**
- **Test addon source:** `/home/node/.openclaw/workspace/project/service.kodi_mcp/`
- **Version source:** `/home/node/.openclaw/workspace/project/service.kodi_mcp/addon.xml`
- **Build output:** `/home/node/.openclaw/workspace/addon/service.kodi_mcp-*.zip`
- **Repo publish:** `/home/node/.openclaw/workspace/repo/dev-repo/zips/service.kodi_mcp/`
- **External (IGNORE):** `/home/node/.openclaw/workspace/kodi_addon/packages/service.kodi_mcp/` — real addon project, out of server's packaging scope

**Dev-loop sequence:**
1. Read version from `project/service.kodi_mcp/addon.xml`
2. Build/package from `project/service.kodi_mcp/` source
3. Publish ZIP to `repo/dev-repo/zips/service.kodi_mcp/`
4. **STOP** — human manual install on remote Kodi required before proceeding

**Critical constraint:** kodi_mcp_server must NOT package kodi_addon. The server's dev-loop is for internal validation only. Real Kodi addon (kodi_mcp_addon) is a separate project.

- **addon.xml version bump REQUIRED** before any commit/push tied to deployment
- **Dev-loop workflow requires human manual install** between stage 2 (publish) and stage 4 (update)

---

## OpenClaw TOP Roles and Rules

### OpenClaw TOP (Orchestration Layer)

The TOP is the orchestration layer that coordinates kodi_mcp_server operations. Its role is **orchestration only** — it does not directly implement server logic.

### When to Use Each Surface

**Runtime surface** (`/runtime/*`):
- Use when addon already installed on remote Kodi
- Use for routine operations: execute, check status, read logs
- Use when you need deterministic, side-effecting operations

**Advisory/diagnostic surface** (`/diagnostic/*`):
- Use when you need to query state without side effects
- Use for version checks, capability discovery, file reads
- Use when you need read-only information

**Dev-loop surface** (`/dev-loop/*`):
- Use when building, publishing, or verifying addon deployments
- Use when you have source code and want to prepare deployment
- Use for `build_addon_package`, `publish_addon_to_repo`, `verify_bridge_addon_deploy`

### When to Require Git Commit/Push

**Worker must commit/push when:**
1. Server code changes (new tools, bug fixes, refactoring)
2. Dev-loop workflow changes (new stages, new tools)
3. ANY change to implementation files in `project/`

**Human approval required before push:**
- All commits require explicit human approval
- Push only when change is complete and tested
- Document changes in commit message

**DO NOT commit/push when:**
- Just reading or analyzing code (no changes)
- Testing locally without persistence
- Documentation changes without implementation changes

### When to Require addon.xml Version Bump

**Mandatory version bump before:**
1. Any commit/push tied to deployment preparation
2. Any `build_addon_package` operation
3. Any `publish_addon_to_repo` operation
4. Any `update_addon` preparation

**Version bump process:**
1. Edit `addon.xml` `version` attribute
2. Edit `CHANGELOG.md` or release notes
3. Commit both files together
4. Push with message: "Bump version to X.Y.Z for deployment"

**Rule:** If you're preparing deployment, bump version first. If you're just fixing bugs, bump version after fixing, before build/publish.

### When to Stop for Manual Install

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

Last updated: 2026-03-31
