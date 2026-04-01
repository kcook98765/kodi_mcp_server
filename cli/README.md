# Kodi CLI

CLI wrapper for the Kodi MCP Server. Provides a thin, deterministic interface to the backend server.

## Installation

```bash
cd /home/node/.openclaw/workspace
python -m venv .venv
source .venv/bin/activate
pip install -e ./cli
```

After installation, you can run `kodi-cli` from anywhere.

## Usage

### Server Configuration

Default server: `http://localhost:8000`

Override with `--server`:
```bash
kodi-cli --server http://my-kodi-server:8000 system status
```

### Output Format

All commands output structured JSON:
- On success: `{"status": "ok", "data": <result>}`
- On error: `{"error": <message>}`

Use `--compact` for inline JSON (no indentation):
```bash
kodi-cli --compact jsonrpc call --method JSONRPC.Version
```

## Commands

### `system status`
Get server/system status.

```bash
kodi-cli system status
```

Output:
```json
{
  "status": "ok",
  "data": {
    "server": {"status": "running"},
    "config": {"loaded": true},
    "jsonrpc": {"status": "ok"},
    "bridge": {"status": "ok"}
  }
}
```

### `jsonrpc call`
Execute a JSON-RPC command.

```bash
kodi-cli jsonrpc call --method JSONRPC.Version
kodi-cli jsonrpc call --method Application.GetProperties --params '{"properties": ["fullname"]}'
```

Output:
```json
{
  "status": "ok",
  "method": "JSONRPC.Version",
  "result": {"version": 21},
  "error": null,
  "error_type": null,
  "error_code": null,
  "latency_ms": 5
}
```

### `addon info`
Get addon info from the bridge.

```bash
kodi-cli addon info --addonid plugin.video.example
```

Output:
```json
{
  "status": "ok",
  "addonid": "plugin.video.example",
  "data": {...}
}
```

### `addon execute`
Execute an addon via the bridge.

```bash
kodi-cli addon execute --addonid plugin.video.example
```

### `builtin exec`
Execute a Kodi builtin command.

```bash
kodi-cli builtin exec --command PlayerControl(Play)
kodi-cli builtin exec --command PlayerControl(Play) --addonid plugin.video.example
```

Output:
```json
{
  "status": "ok",
  "command": "PlayerControl(Play)",
  "data": {...}
}
```

### `log tail`
Get log tail from the bridge.

```bash
kodi-cli log tail --lines 50
```

Output:
```json
{
  "status": "ok",
  "lines": 50,
  "data": {
    "lines": ["log line 1", "log line 2", ...]
  }
}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Invalid arguments |
| 2 | Connection error (server unreachable) |
| 3 | Server error (HTTP error or invalid response) |
| 4 | Request timeout |

## Development

Run tests:
```bash
cd cli
python -m pytest test_cli.py -v
```

## Architecture

This CLI is a thin wrapper around the backend server:

```
agent → kodi-cli → backend server (localhost:8000) → remote Kodi
```

No business logic is duplicated here. All operations delegate to the server's tool endpoints.
