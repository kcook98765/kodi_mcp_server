# Kodi MCP Server

This repository provides an **MCP (Model Context Protocol) server for Kodi**.

It exposes a curated set of Kodi operations (Kodi JSON-RPC + the Kodi MCP bridge addon) as MCP tools, so agent clients (like VS Code/Cline) can control and inspect Kodi in a structured way.

## Connection modes

### 1) MCP over stdio (default / most compatible)

Run by your MCP client (Cline) as a local process:

- Command: `kodi-mcp`
- Transport: stdin/stdout

**Cline config (stdio)**

```json
{
  "mcpServers": {
    "kodi-mcp": {
      "command": "kodi-mcp",
      "args": [],
      "env": {
        "KODI_JSONRPC_URL": "http://kodi.local:8080/jsonrpc",
        "KODI_BRIDGE_BASE_URL": "http://kodi.local:8765"
      }
    }
  }
}
```

### 2) MCP remote transport (Streamable HTTP)

Expose the MCP server over HTTP at:

- `http://<host>:8010/mcp`

The MCP server runs on the host and clients connect over HTTP; **no local process is required**.

Start the server:

```bash
uvicorn kodi_mcp_server.main:app --host 0.0.0.0 --port 8010
```

#### Cline config (remote MCP)

Example **with API key header**:

```json
{
  "mcpServers": {
    "kodi-mcp-remote": {
      "type": "streamableHttp",
      "url": "http://claw.home.arpa:8010/mcp",
      "disabled": false,
      "headers": {
        "x-mcp-api-key": "<optional>"
      }
    }
  }
}
```

Example **without headers** (no API key):

```json
{
  "mcpServers": {
    "kodi-mcp-remote": {
      "type": "streamableHttp",
      "url": "http://claw.home.arpa:8010/mcp",
      "disabled": false
    }
  }
}
```

#### API key (optional)

To require an API key for remote MCP requests, set:

- `MCP_API_KEY=<your key>`

Clients must send:

- `x-mcp-api-key: <your key>`

> Tip (Windows/cmd.exe): use `set "MCP_API_KEY=secret"` to avoid accidental trailing spaces.

### 3) Optional HTTP debug/compatibility endpoints

The same FastAPI app also exposes HTTP endpoints under `/health`, `/status`, and `/tools/*`.

These are useful for debugging and for the included CLI wrapper, but MCP (stdio or remote) is the primary interface.

## Configuration

Required environment variables:
- `KODI_JSONRPC_URL` (e.g. `http://kodi.local:8080/jsonrpc`)
- `KODI_BRIDGE_BASE_URL` (e.g. `http://kodi.local:8765`)

Optional:
- `KODI_JSONRPC_USERNAME`, `KODI_JSONRPC_PASSWORD`
- `KODI_TIMEOUT`
- `MCP_API_KEY` (remote MCP only)

## First connection test (stdio or remote)

Once connected, try these MCP tools first:
1. `kodi_status`
2. `bridge_health`
3. `bridge_runtime_info`

If you’re testing the **remote** transport directly, you can also do a minimal curl initialize:

```bash
curl -i -N http://claw.home.arpa:8010/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-mcp-api-key: <optional>" \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

---

## Optional HTTP endpoints (debug/compatibility)

When you run the FastAPI server (the same one used for remote MCP), these endpoints are also available:

- `GET /health`
- `GET /status`
- `/tools/*` (legacy HTTP endpoints used by `kodi-cli`)

The `/tools/*` endpoints are not the primary integration surface; they exist for debugging and backwards compatibility.

They are **not MCP** and are not used by MCP clients.

## Hosting example (systemd)

Example unit file (Linux). This hosts remote MCP at `http://<host>:8010/mcp`:

```ini
[Unit]
Description=Kodi MCP Server (FastAPI + Remote MCP)
After=network.target

[Service]
Type=simple
User=kodi
WorkingDirectory=/opt/kodi_mcp_server
Environment=KODI_JSONRPC_URL=http://kodi.local:8080/jsonrpc
Environment=KODI_BRIDGE_BASE_URL=http://kodi.local:8765
# Optional (protect /mcp)
Environment=MCP_API_KEY=change-me

ExecStart=/opt/kodi_mcp_server/.venv/bin/uvicorn kodi_mcp_server.main:app --host 0.0.0.0 --port 8010
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## Testing

```bash
python -m pytest -v
```

## Next Steps

1. **Live integration testing** — Validate CLI + server against real remote Kodi
2. **Connection reuse** — Optimize transport with connection pooling
3. **README/API docs** — Expand documentation with examples
4. **Production validation** — Test across network conditions and Kodi states
