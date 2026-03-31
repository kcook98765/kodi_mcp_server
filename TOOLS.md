# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Kodi remote endpoint URLs
- Bridge endpoint configuration
- Local network details for testing
- Any environment-specific setup

## Current Project: kodi_mcp_server

### Important Clarifications

**This is NOT built-in OpenClaw MCP.**

- OpenClaw does not have native MCP support in this context
- This is a **custom backend server** built specifically for Kodi integration
- The server will later be accessed through local CLI wrappers (not direct API calls)
- Think of it as: `agent` → `local CLI` → `kodi_mcp_server` → `remote Kodi`

### Current State

We are working on the **backend server**. This means:

- HTTP endpoints in `project/src/kodi_mcp_server/`
- Transport layers for Kodi communication (`transport/`)
- Tool implementations (`tools/`)
- Config and models (`config.py`, `models/`)

**Do not** pretend this is a generic MCP setup. It's a custom Python server with:
- HTTP JSON-RPC client for Kodi
- HTTP bridge client for the remote Kodi addon
- Repo server for serving Kodi addon packages

### Future CLI Wrappers

When we're ready for agent-facing commands, they will:
- Be thin wrappers that call the server endpoints
- Live outside this repo (or in `project/scripts/`)
- Use structured JSON responses from the server

**Don't write raw HTTP calls in your agent messages.** Write CLI commands that invoke the server.

## Example Entries

```markdown
### Remote Kodi Bridge

- Base URL: `http://kodi.local:8080` (replace with actual)
- Health endpoint: `/health`
- Status endpoint: `/status`

### Kodi JSON-RPC

- URL: `http://kodi.local:8080/jsonrpc`
- Auth: basic auth (username/password in `.env`)

### Local Repo Server

- Port: 8001
- Serves addon packages from `project/repo/`
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
