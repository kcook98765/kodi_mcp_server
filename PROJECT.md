# PROJECT.md - kodi_mcp_server

## Overview

**kodi_mcp_server** is a custom Python middle-layer server for remote Kodi integration.

This is **not** built-in OpenClaw MCP support. This is a custom server built specifically to sit between:

1. **Local CLI wrappers** (future) — thin commands the agent invokes
2. **Remote Kodi addon / bridge endpoints** — HTTP API for Kodi control
3. **Kodi JSON-RPC** — native Kodi protocol over HTTP
4. **Kodi repo server** — serving addon packages locally

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

**In progress** — backend server stabilization.

### What Exists

- HTTP server with `uvicorn`
- Two main app modules: `mcp_app` and `repo_app`
- Transport layers: `HttpJsonRpcTransport`, `HttpBridgeClient`, `MockTransport`
- Message models: `RequestMessage`, `ResponseMessage`
- Config loading from environment variables
- Helper scripts for repo/addon operations

### What's Needed

- Stabilize configuration loading (currently loads from `.env` in `mcp_repo_server/`)
- Standardize error handling and responses
- Define clear contract for future CLI wrappers
- Fill gaps in tool implementations
- Write integration tests for transport layers

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

---

Last updated: 2026-03-31
