# Backend server health checks
# Add tasks below when you want the agent to check something periodically.

- **Server health:** curl http://localhost:8000/health (or check if process running)
- **Config sanity:** verify .env has KODI_JSONRPC_URL, KODI_BRIDGE_BASE_URL
- **Git status:** any uncommitted changes in workspace root or project/?
- **Remote reachability:** can we reach Kodi bridge and JSON-RPC endpoints?
- **CURRENT_STATE.md review:** any gaps needing attention? any updates since last check?
