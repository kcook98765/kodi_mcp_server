# Kodi MCP Server

This repository provides an **MCP (Model Context Protocol) server for Kodi**.

It exposes a curated set of Kodi operations (Kodi JSON-RPC + the Kodi MCP bridge addon) as MCP tools, so agent clients (like VS Code/Cline) can control and inspect Kodi in a structured way.

## Quick start

> For the canonical repo publish/install/update behavior, see:
> **project-config/REPO_WORKFLOW_RUNBOOK.md**
>
> For the current local handoff state and next TODOs, see:
> **project-config/CURRENT_STATE.md**

**Server install**
1) Clone this repository on the host that will run the MCP server.
2) Create a virtual environment and install the package:
```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```
3) Copy `.env.example` to `.env` and set at least:
```bash
KODI_JSONRPC_URL=http://<kodi-host>:8080/jsonrpc
KODI_BRIDGE_BASE_URL=http://<kodi-host>:8765
KODI_BRIDGE_TOKEN=<same-token-configured-in-service.kodi_mcp>
REPO_BASE_URL=http://<server-host>:8010
```
`REPO_BASE_URL` must be reachable from Kodi and any remote MCP client that needs repository files or screenshot URLs.
4) Start the server:
```bash
.venv/bin/uvicorn kodi_mcp_server.main:app --host 0.0.0.0 --port 8010
```

**First-time bridge bootstrap (ordinary remote user)**

Kodi 19–22 do not expose a stock JSON-RPC method that installs an arbitrary ZIP,
adds a repository/source, or executes Kodi's `InstallAddon`/`InstallFromZip`
built-ins. A bridge-absent target therefore requires a one-time user-mediated
Kodi flow; Kodi MCP does not bypass that security boundary or silently enable
Unknown Sources.

1) The server operator provides the official `service.kodi_mcp` release ZIP and
matching `bridge-bootstrap.json`. In a source checkout, the release maintainer can
prepare the exact authoritative bundle without contacting Kodi:
```bash
.venv/bin/python scripts/prepare_bridge_bootstrap.py --source /path/to/kodi_mcp_addon
```
2) Set `REPO_BASE_URL` to the HTTPS URL Kodi/users can reach. The default manifest
path is `addon/bridge-bootstrap.json`; override it with
`KODI_MCP_BRIDGE_BOOTSTRAP_MANIFEST` when needed.
3) Start the server and call `bridge_bootstrap_status`.
4) If it returns `state=user_action_required`, compare the returned SHA-256, make
that exact ZIP visible to Kodi (download on the Kodi device or use a user-approved
network source), then use **Add-ons → Install from zip file**. Review Kodi's Unknown
Sources warning yourself; MCP will not change the setting.
5) Configure **service.kodi_mcp → Kodi MCP → MCP shared token** to match
`KODI_BRIDGE_TOKEN`, then call `bridge_bootstrap_status` again.
6) Do not treat installation as complete until the result is
`state=already_installed`, `verified=true`, and `next_stage=managed_deployment`.
This checks Kodi metadata plus the running bridge's exact version, source Git SHA,
and source fingerprint. A wrong build is routed to the existing authoritative
update/normalization flow instead of being accepted.

The bootstrap status operation is read-only and idempotent. Cancellation,
interrupted installation, a disabled/unconfigured service, an unavailable bundle,
or an identity mismatch remains an explicit non-success state on the next call.
Future bridge upgrades use the existing authoritative update flow; the one-time ZIP
step is not repeated.

**First-time repository/managed-addon onboarding (after bridge verification)**
1) Start this server and confirm `bridge_bootstrap_status` is verified.
2) Install + launch the Kodi setup helper addon: `script.kodi_mcp_setup`.
3) In **Kodi MCP Setup**, confirm the server URL and choose **Prepare repository add-on zip**.
4) Choose **Open Install from zip file**, then select `repository.kodi-mcp-latest.zip`.
5) For a brand-new target addon, use Kodi UI: **Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**.

**Managed addon loop (after repo is installed in Kodi)**
1) Register local addon: `managed_addon_register`
2) Build/publish/stage/apply: `managed_addon_build_publish_stage_and_apply`
3) If needed: `managed_addon_validate_state`

Success = `verification.apply_verified == true`
Retry only if `verification.can_retry == true`

## Kodi addon requirement

- Rich bridge-backed control requires `service.kodi_mcp` to be installed, enabled,
  and configured with the shared token:
  https://github.com/kcook98765/kodi_mcp_addon
- Before the bridge exists, Kodi MCP can still use stock JSON-RPC for status and
  `bridge_bootstrap_status`; bridge-dependent tools remain unavailable.
- The server exposes only the validated configured bridge bundle at
  `/bootstrap/manifest.json` and `/bootstrap/service.kodi_mcp.zip`.
- After the one-time user-mediated ZIP install, the bridge supplies GUI, health,
  staging, and authoritative update capabilities.
- Kodi-resident bridge addon source is owned by the standalone `kodi_mcp_addon`
  repo, not this server repo.

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
      "url": "http://<server-host>:8010/mcp",
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
      "url": "http://<server-host>:8010/mcp",
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
- `REPO_BASE_URL` for repo, first-install bridge bundle, and screenshot URLs visible to Kodi/clients on other hosts
- `KODI_MCP_BRIDGE_BOOTSTRAP_MANIFEST` for the pinned bridge bundle manifest (default `addon/bridge-bootstrap.json`)
- `KODI_SCREENSHOT_STORE_DIR`, `KODI_SCREENSHOT_RETENTION_SECONDS`, `KODI_SCREENSHOT_MAX_FILES`
- `KODI_VISION_MODEL_URL`, `KODI_VISION_MODEL_NAME`; when unset, screenshot capture remains available but vision-analysis tools are not exposed

For a one-host setup, these URLs may all use `localhost`. For a split-host setup, use hostnames or IPs that are reachable from the machine that consumes each URL:

- Kodi/users must reach `REPO_BASE_URL` for repository files and the one-time bridge bundle.
- The server must reach `KODI_JSONRPC_URL`; bridge-backed tools additionally require `KODI_BRIDGE_BASE_URL`.
- Remote MCP clients must reach `http://<server-host>:8010/mcp` and screenshot URLs returned under `/screenshots/`.

Local development can use a repo-root `.env` file copied from `.env.example`.
Process environment values take precedence over `.env` values. Keep `.env`,
`.env.*`, local backups, keys, and logs out of Git.

## First connection test (stdio or remote)

Once connected, try these MCP tools first:
1. `kodi_status`
2. `bridge_bootstrap_status` (works through stock JSON-RPC when the bridge is absent)
3. `bridge_health`
4. `bridge_runtime_info`

GUI helpers:
- `kodi_gui_action` sends basic navigation actions (`up`, `down`, `left`, `right`, `select`, `back`, `home`, `context`, `info`) and the cleanup action `stop`.
- `kodi_gui_screenshot` captures a Kodi GUI screenshot through the bridge addon, stores it on the MCP server by default, and returns a `/screenshots/<id>.png` URL.
- `kodi_gui_state` returns compact Kodi window/control/player state for UI verification.
- `addon_execute` launches an addon through Kodi JSON-RPC without using the legacy HTTP companion endpoint. It returns post-launch `gui_state` by default. Use `expect_window` / `expect_fullscreen` for UI or navigation addons; reserve `expect_player` for tasks that explicitly require media playback.
- These can assist first-install UI navigation, but deterministic bridge/repo state checks should remain the primary workflow.

Addon source and log triage helpers:
- `addon_source_inspect` reads addon identity, extensions, Python entrypoints, tests, and `PROJECT_MAP.md` status from an allowlisted server-local source tree or known agent mount such as `/srv/workspaces/...`.
- `addon_project_map_status` reports whether an addon source tree has `PROJECT_MAP.md`.
- `addon_source_tree` returns a compact addon file tree for agent planning.
- `bridge_log_recent_errors` filters recent bridge/Kodi log lines down to error-like entries, with an optional pattern.

Video-library discovery helpers:
- `kodi_library_summary` returns movie, TV-show, season, and episode totals from Kodi's native `limits.total` metadata. Each count call requests at most one sentinel item; it never downloads the full library.
- `kodi_library_search` searches one explicit media type (`movie`, `tvshow`, or `episode`) using Kodi's native **title `contains`** filter. This is deterministic substring search, not fuzzy, semantic, cast, plot, filename, or path search. Matching is case-insensitive on the supported Kodi 19–22 targets.
- `kodi_library_browse` exposes bounded `recent_movies`, `recent_episodes`, `movie_genres`, `tvshow_genres`, `movie_sets`, `movie_tags`, and `tvshow_tags` views.
- `kodi_tv_seasons` accepts a `tvshow_id` returned by search and lists that show's seasons. `kodi_tv_episodes` accepts the same ID plus a season number and lists episodes.
- Every listing/search uses Kodi-side `start`/`end` limits. `limit` defaults to 10 and is rejected above the hard maximum of 50. Results report `start`, `end`, `total`, requested `limit`, and `has_more`.
- Results include stable Kodi IDs and concise identifying/play-state metadata. Raw media files and local paths are intentionally omitted. Artwork is limited to three useful references and drops filesystem/network-share references, credential-bearing URLs, and common token-bearing URLs.

A movie workflow from unknown contents to existing playback:
```json
{"tool":"kodi_library_search","arguments":{"query":"Alien","media_type":"movie","limit":5}}
{"tool":"kodi_player_open","arguments":{"media_type":"movie","item_id":123}}
```
Use the `id` returned by the first call as `item_id`; do not copy the illustrative ID above.

A TV hierarchy workflow:
```json
{"tool":"kodi_library_search","arguments":{"query":"Example Show","media_type":"tvshow","limit":5}}
{"tool":"kodi_tv_seasons","arguments":{"tvshow_id":456,"limit":20}}
{"tool":"kodi_tv_episodes","arguments":{"tvshow_id":456,"season":1,"limit":20}}
```
Again, use the discovered `tvshow` ID. Empty pages are successful with `empty:true`; an invalid/nonexistent TV-show ID is a model-visible `not_found` error.

Music-library discovery helpers:
- `kodi_music_summary` returns artist, album, and song totals from native `limits.total`; each of its three fixed count calls requests at most one sentinel item.
- `kodi_music_search` searches exactly one type: artist name (`artist`), album title (`album`), or song title (`title`). It uses Kodi's native case-insensitive `contains` operator on the supported Kodi 19–22 targets. It is substring search, not fuzzy, semantic, cross-type ranking, lyrics, filename, or path search.
- `kodi_music_browse` exposes bounded `recent_albums`, `recent_songs`, and `genres` pages.
- `kodi_artist_albums` validates a discovered artist ID, then lists albums for that artist without implicitly broadening to contributor-only roles. `kodi_album_songs` validates an album ID, then lists its songs in Kodi's native track order. Multi-artist names and IDs and compilation status remain explicit.
- All music pages default to 10 results, reject limits above 50, use Kodi-side pagination, and return native totals. Results omit raw media paths and bound nested artist/genre lists to 20 values. Artwork is reference-only, limited to three entries, and uses the same filesystem/share/credential/token screening as video discovery.

A music workflow from unknown contents to normal audio playback:
```json
{"tool":"kodi_music_search","arguments":{"query":"Example Artist","media_type":"artist","limit":5}}
{"tool":"kodi_artist_albums","arguments":{"artist_id":123,"limit":10}}
{"tool":"kodi_album_songs","arguments":{"album_id":456,"limit":20}}
{"tool":"kodi_player_open","arguments":{"media_type":"song","item_id":789}}
{"tool":"kodi_player_active","arguments":{}}
{"tool":"kodi_player_item","arguments":{"playerid":0}}
```
Use IDs returned by the preceding call, not the illustrative IDs above. Kodi normally reports the active audio player as ID `0`; discover it with `kodi_player_active` rather than assuming. Pass that returned ID to item, pause, seek, and stop, and stop playback after controlled tests. `kodi_player_open` also accepts `media_type:"album"`; Kodi's native album item starts immediate album playback without this server clearing, replacing, or enqueueing a playlist.

Playback helpers:
- `kodi_player_active` returns active Kodi players.
- `kodi_player_item` returns the current item for a player.
- `kodi_player_seek` seeks a player to an absolute timestamp in seconds.
- `kodi_player_pause` pauses without toggling playback back on.
- `kodi_player_stop` stops a player and, by default, verifies playback stays inactive across a short settle window.

Autonomous agents should use these MCP tools instead of direct Kodi JSON-RPC,
bridge HTTP, host-control scripts, or curl fallbacks. If a required operation is
missing from MCP, add a curated MCP tool rather than teaching agents a new escape
hatch.

### Structured MCP results and annotations

`tools/list` advertises Draft 2020-12 `outputSchema` contracts for every stable
result tool. Calls to those tools return the existing JSON envelope in `TextContent`
for backwards compatibility and a corresponding validated envelope in
`structuredContent`. The envelope covers both success and application-level
failure (`ok`, `tool`, `data`, `error`, `error_type`, `error_code`, latency,
request identity, and raw diagnostics). Tool failures remain model-visible with
`isError: true` and a meaningful error.

Stable result families add required fields for status, GUI state, screenshots,
bounded logs, source inspection, active players, and managed-addon validation.
Pass-through and evolving mutation results use intentionally broader data
schemas inside the stable envelope rather than guessed narrow fields. Two highly
heterogeneous tools intentionally remain schema-less and text-only:
`addon_execute` (optional player/GUI verification changes its shape) and
`jsonrpc_introspect` (the Kodi API description varies by version and options).

Compatibility details:

- Screenshot metadata is structured, while image bytes remain a canonical MCP
  `ImageContent` block when requested and within the inline limit. Base64 is not
  copied into `structuredContent`.
- Log text remains in the bounded compatibility `TextContent`. Structured log
  output contains truncation/count/byte metadata only, so a large log is not
  duplicated.
- Every tool has standardized MCP behavior hints. Read-only hints are used only
  for operations that do not modify state; mutating tools are split between
  additive/non-destructive and potentially destructive operations. Idempotency
  is not claimed for mutations, and only arbitrary addon execution is marked as
  open-world.
- The server validates structured results against the exact schema advertised by
  `tools/list`. A mismatch fails closed as an `output_contract_error` instead of
  emitting a misleading successful structured result.

If you’re testing the **remote** transport directly, you can also do a minimal curl initialize:

```bash
curl -i -N http://<server-host>:8010/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-mcp-api-key: <optional>" \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## Managed addon development workflow (golden path)

### Repo workflow (publish/install/update)

The repo system has a known rule: **brand-new addons require a one-time manual install in Kodi UI**, and updates can be automated after that.

See the runbook:
- **project-config/REPO_WORKFLOW_RUNBOOK.md**

### Prerequisites
- `KODI_BRIDGE_BASE_URL` set (bridge addon HTTP base URL)
- `KODI_BRIDGE_TOKEN` set
  - Must match Kodi addon setting: **service.kodi_mcp → mcp_token**
- Kodi is running with **service.kodi_mcp enabled**

Note: first-time repo installation no longer requires a separate staging action — the server auto-stages the current dev repo zip once registration is healthy.

### Tool call sequence (MCP)

1) Register the local addon source folder (must contain `addon.xml`):
```json
{ "source_path": "C:/dev/addons/plugin.video.foo" }
```

2) Build → publish into dev repo → build dev repo zip → stage to Kodi:
```json
{
  "managed_addon_id": "plugin.video.foo",
  "version_policy": "bump_patch",
  "repo_version": "2026.04.08.1",
  "verify": true
}
```

3) Validate state (fast read-only readiness report):
```json
{ "managed_addon_id": "plugin.video.foo" }
```

### Autonomous iteration (agent loop)

Example tool call:
```json
{
  "managed_addon_id": "plugin.video.foo",
  "version_policy": "bump_patch",
  "repo_version": "2026.04.08.1",
  "verify": true
}
```

Success signal (only reliable): `verification.apply_verified == true`

For split-host agents that already built a zip, prefer the pathless artifact workflow:

1. `artifact_upload_zip`
2. `repo_publish_stage_apply_artifact` or its agent-oriented alias `addon_dev_loop`
3. `kodi_gui_state`, `kodi_gui_screenshot`, `kodi_player_*`, or addon-specific checks for visual/behavioral evidence

`artifact_upload_zip` validates the zip structure and `addon.xml`; `repo_publish_stage_apply_artifact` / `addon_dev_loop` publishes, stages, applies, and returns `apply_verified`, `installed_version_after`, `apply_status`, `can_retry`, and `failure_reason` in one response.

Retry behavior:
- Retry only when `verification.can_retry == true`
- Sleep `verification.retry_delay_seconds` (if present)
- Stop when `verification.can_retry == false`
- Use `verification.retry_hint` as the operator-readable reason

Key `verification.apply_status` values:
- `applied` — version changed to target
- `already_current` — target already installed
- `repo_not_installed` — one-time repo install required
- `repo_not_ready` — repo refresh/metadata not ready
- `addon_not_found` — addon not visible in repo metadata
- `*_attempted_not_verified` — install/update requested but not confirmed
- `bridge_unreachable` — Kodi bridge not reachable
- `failed` — unknown failure (inspect output)

Operator rule: If the loop cannot complete, run `managed_addon_validate_state` and follow its output.

### Kodi-side manual step (required)
1) Follow `bridge_bootstrap_status` for the one-time exact bridge ZIP install; do not bypass Kodi's Unknown Sources warning.
2) Configure the token:
   **Kodi → Add-ons → Services → Kodi MCP Service → Configure → Kodi MCP → MCP shared token**
3) Re-run `bridge_bootstrap_status` and require exact identity verification.
4) Install + enable **Kodi MCP Repository** (`repository.kodi-mcp`) once if it is missing
5) For each brand-new target addon:
   **Kodi → Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**
6) Rerun the managed addon apply/update workflow after the first install

Note: a staged `dev-repo.zip` is repository content used by the server/bridge refresh loop; it is not itself an installable Kodi add-on zip.

Troubleshooting rule: **If anything fails, run `managed_addon_validate_state` first.**

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

## Troubleshooting

### Kodi bridge unreachable
Symptom: `kodi_status` reports JSON-RPC `ok` but bridge `error`, or managed apply reports `bridge_unreachable`.
Action:
- Run `bridge_bootstrap_status` first.
- If `installed=false`, follow its one-time user action and exact artifact identity.
- If `installed=true`, ensure the service is enabled and its token matches.
- Never enable Unknown Sources or install an unverified same-ID ZIP automatically.

### Repo not installed (first-time setup)
Symptom: `apply_status = repo_not_installed`; `dev_setup_available` may be true.
Action:
- Install `repository.kodi-mcp` once.
- Then use **Add-ons → Install from repository → Kodi MCP Repository → target addon → Install**.
- Do not try to install the staged `dev-repo.zip`; it is repository content, not an installable add-on zip.

### Repo not ready / refresh lag
Symptom: `apply_status = repo_not_ready`.
Action:
- Wait a few seconds and retry
- Or manually run “Check for updates” in Kodi

### Addon not found in repo
Symptom: `apply_status = addon_not_found`.
Action:
- Retry once
- If still failing: confirm repo installed and repo zip staged correctly (`managed_addon_validate_state`)

### Install/update not verified
Symptom: `apply_status = install_attempted_not_verified` or `update_attempted_not_verified`.
Action:
- Retry (short delay)
- If persistent: verify repo enabled and check Kodi update settings (optionally trigger update manually)

### Unknown failure
Symptom: `apply_status = failed`.
Action:
- Run `managed_addon_validate_state`
- Inspect: artifacts, repo_ready_check, bridge state

## Testing

```bash
python -m pytest -v
```
