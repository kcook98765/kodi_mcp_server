# kodi_mcp_server workspace

OpenClaw-controlled workspace for custom Kodi middle-layer server.

## Structure

- **project/** - Server implementation code (local development workspace)
- **cli/** - CLI wrapper for agent use (git-tracked)
- **Workspace root** - OpenClaw config, documentation, and guidance files

## Quick Start

**Start the server:**
```bash
cd project
uvicorn kodi_mcp_server.main:app --port 8000
```

**Use the CLI wrapper:**
```bash
./kodi-cli system status
./kodi-cli jsonrpc call --method JSONRPC.Version
./kodi-cli addon info --addonid plugin.video.example
./kodi-cli builtin exec --command PlayerControl(Play)
```

## Testing

**Backend tests (mocked):**
```bash
cd project
.venv/bin/pytest tests/ -v
```

**CLI tests:**
```bash
cd cli
.venv/bin/pytest test_cli.py -v
```

## Documentation

- **CURRENT_STATE.md** - Project state and task tracking
- **cli/README.md** - CLI wrapper documentation
- **project/README.md** - Server API documentation (local)
