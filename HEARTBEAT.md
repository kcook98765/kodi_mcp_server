# Backend server health checks
# Add tasks below when you want the agent to check something periodically.

- **Server health:** curl http://localhost:8000/health (or check if process running)
- **Config sanity:** verify .env has KODI_JSONRPC_URL, KODI_BRIDGE_BASE_URL
- **Git status:** any uncommitted changes in workspace root or project/?
- **Remote reachability:** can we reach Kodi bridge and JSON-RPC endpoints?
- **CURRENT_STATE.md review:** any gaps needing attention? any updates since last check?

---

## Recent Changes (2026-04-04)

**Repository.kodi-mcp manifest fix - DEV-LOOP:**
- Code: workspace commit 4340429 (master), project commit 4d6d4d1 (main)
- Fixed: Removed xbmc.service and xbmc.python.pluginsource from repository addon
- Fixed: Replaced jar://file:// URLs with HTTP URLs for info/checksum/datadir
- Fixed: Added proper <dir> block structure for Kodi repository manifest
- Deployed: Updated repo/install/ and repo-addon/ to serve 1.0.2 (fixed manifest)
- Live URL: http://claw.home.arpa:8000/repo/install/latest.zip (now returns 200 with 1.0.2)
- Status: READY FOR INSTALl - Kodi should accept this without "invalid add-on type name" error

**Current state:** Backend server not running. Workspace clean with 4340429 pushed to origin/master. Project clean with 4d6d4d1 pushed to origin/main.
