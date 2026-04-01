"""MCP/API tool registration for kodi_mcp_server."""

from fastapi import Query

from kodi_mcp_server.app_shared import (
    build_addon_ops_tool,
    build_bridge_tool,
    build_jsonrpc_tool,
    build_notification_probe,
    build_repo_tool,
)
from kodi_mcp_server.models.requests import (
    ExecuteAddonRequest,
    ExecuteBuiltinRequest,
    EnsureAddonEnabledRequest,
    PublishAddonRequest,
    RestartBridgeAddonRequest,
    UpdateAddonRequest,
    UploadAddonZipRequest,
    WriteLogMarkerRequest,
)


def configure_mcp_app(app):
    """Register MCP-facing API routes on the shared app."""

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": "kodi_mcp_server"}

    @app.get("/status")
    async def full_status():
        """Check server health and connectivity to all downstream services."""
        from kodi_mcp_server.config import KODI_JSONRPC_URL, KODI_BRIDGE_BASE_URL

        result = {
            "server": {"status": "running"},
            "config": {"loaded": bool(KODI_JSONRPC_URL and KODI_BRIDGE_BASE_URL)},
            "jsonrpc": {"status": "unknown", "url": KODI_JSONRPC_URL},
            "bridge": {"status": "unknown", "url": KODI_BRIDGE_BASE_URL},
        }

        # Test JSON-RPC connectivity (simple ping)
        if KODI_JSONRPC_URL:
            try:
                from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport
                from kodi_mcp_server.models.messages import RequestMessage
                import uuid

                transport = HttpJsonRpcTransport(
                    url=KODI_JSONRPC_URL,
                    username="",
                    password="",
                    timeout=5,
                )
                request = RequestMessage(
                    request_id=str(uuid.uuid4()),
                    command="execute_jsonrpc",
                    args={"method": "JSONRPC.Version", "params": {}},
                )
                response = await transport.send_request(request)
                if response.error:
                    result["jsonrpc"]["status"] = "error"
                    result["jsonrpc"]["error"] = response.error
                else:
                    result["jsonrpc"]["status"] = "ok"
            except Exception as e:
                result["jsonrpc"]["status"] = "error"
                result["jsonrpc"]["error"] = str(e)

        # Test bridge connectivity
        if KODI_BRIDGE_BASE_URL:
            try:
                from kodi_mcp_server.transport.http_bridge import HttpBridgeClient

                client = HttpBridgeClient(base_url=KODI_BRIDGE_BASE_URL, timeout=5)
                response = await client.get_health()
                if response.error:
                    result["bridge"]["status"] = "error"
                    result["bridge"]["error"] = response.error
                else:
                    result["bridge"]["status"] = "ok"
            except Exception as e:
                result["bridge"]["status"] = "error"
                result["bridge"]["error"] = str(e)

        return result

    @app.get("/")
    async def root():
        return {"message": "Kodi MCP Server running"}

    @app.get("/tools/get_kodi_log_tail")
    async def get_kodi_log_tail_endpoint(
        limit: int = Query(default=100, ge=1),
    ):
        result = await build_bridge_tool().get_bridge_log_tail(lines=limit)
        return result.to_dict()

    @app.get("/tools/get_bridge_health")
    async def get_bridge_health_endpoint():

        result = await build_bridge_tool().get_bridge_health()
        return result.to_dict()

    @app.get("/tools/get_bridge_status")
    async def get_bridge_status_endpoint():

        result = await build_bridge_tool().get_bridge_status()
        return result.to_dict()

    @app.get("/tools/get_bridge_runtime_info")
    async def get_bridge_runtime_info_endpoint():

        result = await build_bridge_tool().get_bridge_runtime_info()
        return result.to_dict()

    @app.get("/tools/get_bridge_file")
    async def get_bridge_file_endpoint(path: str):
        result = await build_bridge_tool().get_bridge_file(path=path)
        return result.to_dict()

    @app.get("/tools/get_bridge_addon_info")
    async def get_bridge_addon_info_endpoint(addonid: str):
        result = await build_bridge_tool().get_bridge_addon_info(addonid=addonid)
        return result.to_dict()

    @app.get("/tools/get_bridge_log_tail")
    async def get_bridge_log_tail_endpoint(lines: int = Query(default=20, ge=1)):
        result = await build_bridge_tool().get_bridge_log_tail(lines=lines)
        return result.to_dict()

    @app.get("/tools/get_bridge_log_markers")
    async def get_bridge_log_markers_endpoint(lines: int = Query(default=100, ge=1)):
        result = await build_bridge_tool().get_bridge_log_markers(lines=lines)
        return result.to_dict()

    @app.post("/tools/write_bridge_log_marker")
    async def write_bridge_log_marker_endpoint(request: WriteLogMarkerRequest):
        result = await build_bridge_tool().write_bridge_log_marker(message=request.message)
        return result.to_dict()

    @app.post("/tools/bridge_debug_ping")
    async def bridge_debug_ping_endpoint():
        result = await build_bridge_tool().bridge_debug_ping()
        return result.to_dict()

    @app.post("/tools/execute_bridge_builtin")
    async def execute_bridge_builtin_endpoint(request: ExecuteBuiltinRequest):
        result = await build_bridge_tool().execute_bridge_builtin(
            command=request.command,
            addonid=request.addonid,
        )
        return result.to_dict()

    @app.post("/tools/ensure_bridge_addon_enabled")
    async def ensure_bridge_addon_enabled_endpoint(request: EnsureAddonEnabledRequest):
        result = await build_bridge_tool().ensure_bridge_addon_enabled(addonid=request.addonid)
        return result.to_dict()

    @app.post("/tools/execute_bridge_addon")
    async def execute_bridge_addon_endpoint(request: ExecuteAddonRequest):
        result = await build_bridge_tool().execute_bridge_addon(addonid=request.addonid)
        return result.to_dict()

    @app.get("/tools/check_bridge_addon_version")
    async def check_bridge_addon_version_endpoint(addonid: str, expected_version: str):
        result = await build_bridge_tool().check_bridge_addon_version(
            addonid=addonid,
            expected_version=expected_version,
        )
        return result.to_dict()

    @app.get("/tools/verify_bridge_addon_deploy")
    async def verify_bridge_addon_deploy_endpoint(addonid: str, expected_version: str):
        result = await build_bridge_tool().verify_bridge_addon_deploy(
            addonid=addonid,
            expected_version=expected_version,
        )
        return result.to_dict()

    @app.post("/tools/upload_bridge_addon_zip")
    async def upload_bridge_addon_zip_endpoint(request: UploadAddonZipRequest):
        result = await build_bridge_tool().upload_bridge_addon_zip(local_zip_path=request.local_zip_path)
        return result.to_dict()

    @app.get("/tools/listen_kodi_notifications")
    async def listen_kodi_notifications_endpoint(
        sample_size: int = Query(default=3, ge=1),
        listen_seconds: int = Query(default=5, ge=1),
    ):
        probe = build_notification_probe()
        result = await probe.listen(sample_size=sample_size, listen_seconds=listen_seconds)
        return result.to_dict()

    @app.get("/tools/validate_kodi_notifications")
    async def validate_kodi_notifications_endpoint(
        addonid: str = Query(default="script.viewer.sprites_zoom"),
        wait: bool = Query(default=False),
        sample_size: int = Query(default=3, ge=1),
        listen_seconds: int = Query(default=5, ge=1),
    ):
        probe = build_notification_probe()
        tool = build_jsonrpc_tool()
        result = await probe.listen_with_trigger(
            sample_size=sample_size,
            listen_seconds=listen_seconds,
            trigger=lambda: tool.run_addon_and_report(addonid=addonid, wait=wait),
            trigger_name=f"run_addon_and_report:{addonid}:wait={str(wait).lower()}",
        )
        return result.to_dict()

    @app.get("/tools/execute_jsonrpc")
    async def execute_jsonrpc_endpoint(method: str): 
        result = await build_jsonrpc_tool().execute_jsonrpc(method=method)
        return result.to_dict()

    @app.get("/tools/get_application_properties")
    async def get_application_properties_endpoint():

        result = await build_jsonrpc_tool().get_application_properties()
        return result.to_dict()

    @app.get("/tools/get_active_players")
    async def get_active_players_endpoint():

        result = await build_jsonrpc_tool().get_active_players()
        return result.to_dict()

    @app.get("/tools/get_movies_sample")
    async def get_movies_sample_endpoint(
        limit: int = Query(default=5, ge=1),
    ):
        result = await build_jsonrpc_tool().get_movies_sample(limit=limit)
        return result.to_dict()

    @app.get("/tools/get_tvshows_sample")
    async def get_tvshows_sample_endpoint(
        limit: int = Query(default=5, ge=1),
    ):
        result = await build_jsonrpc_tool().get_tvshows_sample(limit=limit)
        return result.to_dict()

    @app.get("/tools/get_sources")
    async def get_sources_endpoint(
        media: str = Query(default="files"),
    ):
        result = await build_jsonrpc_tool().get_sources(media=media)
        return result.to_dict()

    @app.post("/tools/publish_addon_to_repo")
    async def publish_addon_to_repo_endpoint(request: PublishAddonRequest):
        result = await build_repo_tool().publish_addon_to_repo(
            addon_zip_path=request.addon_zip_path,
            addon_id=request.addon_id,
            addon_name=request.addon_name,
            addon_version=request.addon_version,
            provider_name=request.provider_name,
        )
        return result.to_dict()

    @app.get("/tools/wait_for_addon_version")
    async def wait_for_addon_version_endpoint(
        addonid: str,
        version: str,
        timeout_seconds: int = Query(default=30, ge=1),
        poll_interval_seconds: int = Query(default=4, ge=1),
    ):
        result = await build_addon_ops_tool().wait_for_addon_version(
            addonid=addonid,
            version=version,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        return result.to_dict()

    @app.post("/tools/update_addon")
    async def update_addon_endpoint(request: UpdateAddonRequest):
        result = await build_addon_ops_tool().update_addon(
            addonid=request.addonid,
            timeout_seconds=request.timeout_seconds,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        return result.to_dict()

    @app.post("/tools/restart_bridge_addon")
    async def restart_bridge_addon_endpoint(request: RestartBridgeAddonRequest):
        result = await build_addon_ops_tool().restart_bridge_addon(timeout_seconds=request.timeout_seconds)
        return result.to_dict()

    @app.get("/tools/get_addons")
    async def get_addons_endpoint():
        result = await build_jsonrpc_tool().get_addons()
        return result.to_dict()

    @app.get("/tools/get_addon_details")
    async def get_addon_details_endpoint(addonid: str): 
        result = await build_jsonrpc_tool().get_addon_details(addonid=addonid)
        return result.to_dict()

    @app.get("/tools/set_addon_enabled")
    async def set_addon_enabled_endpoint(
        addonid: str,
        enabled: bool,
    ):
        result = await build_jsonrpc_tool().set_addon_enabled(addonid=addonid, enabled=enabled)
        return result.to_dict()

    @app.get("/tools/list_directory")
    async def list_directory_endpoint(
        path: str = Query(default=r"C:\Users\kcook\Kodi_NFO_Sidecar_Test\\"),
        media: str = Query(default="files"),
        limit: int | None = Query(default=None, ge=1),
    ):
        limits = {"start": 0, "end": limit} if limit is not None else None
        result = await build_jsonrpc_tool().list_directory(
            path=path,
            media=media,
            properties=["title", "file"],
            limits=limits,
        )
        return result.to_dict()

    @app.get("/tools/get_recent_movies")
    async def get_recent_movies_endpoint(
        limit: int = Query(default=5, ge=1),
    ):
        result = await build_jsonrpc_tool().get_recent_movies(limit=limit)
        return result.to_dict()

    @app.get("/tools/get_player_item")
    async def endpoint():
        result = await build_jsonrpc_tool().get_player_item()
        return result.to_dict()

    @app.get("/tools/list_addons")
    async def list_addons_endpoint(
        type: str | None = Query(default=None),
        enabled: bool | None = Query(default=None),
    ):
        result = await build_jsonrpc_tool().list_addons(type=type, enabled=enabled)
        return result.to_dict()

    @app.get("/tools/execute_addon")
    async def execute_addon_endpoint(
        addonid: str,
        wait: bool = Query(default=False),
    ):
        result = await build_jsonrpc_tool().execute_addon(addonid=addonid, wait=wait)
        return result.to_dict()

    @app.get("/tools/get_setting_value")
    async def get_setting_value_endpoint(setting: str): 
        result = await build_jsonrpc_tool().get_setting_value(setting=setting)
        return result.to_dict()

    @app.get("/tools/get_system_properties")
    async def endpoint():
        result = await build_jsonrpc_tool().get_system_properties()
        return result.to_dict()

    @app.get("/tools/inspect_addon")
    async def inspect_addon_endpoint(addonid: str): 
        result = await build_jsonrpc_tool().inspect_addon(addonid=addonid)
        return result.to_dict()

    @app.get("/tools/is_addon_installed")
    async def is_addon_installed_endpoint(addonid: str): 
        result = await build_jsonrpc_tool().is_addon_installed(addonid=addonid)
        return result.to_dict()

    @app.get("/tools/is_addon_enabled")
    async def is_addon_enabled_endpoint(addonid: str): 
        result = await build_jsonrpc_tool().is_addon_enabled(addonid=addonid)
        return result.to_dict()

    @app.get("/tools/ensure_addon_enabled")
    async def ensure_addon_enabled_endpoint(addonid: str): 
        result = await build_jsonrpc_tool().ensure_addon_enabled(addonid=addonid)
        return result.to_dict()

    @app.get("/tools/run_addon_and_report")
    async def run_addon_and_report_endpoint(
        addonid: str,
        wait: bool = Query(default=False),
    ):
        result = await build_jsonrpc_tool().run_addon_and_report(addonid=addonid, wait=wait)
        return result.to_dict()

    @app.get("/tools/get_jsonrpc_version")
    async def endpoint():
        result = await build_jsonrpc_tool().get_jsonrpc_version()
        return result.to_dict()

    @app.get("/tools/introspect_jsonrpc")
    async def introspect_jsonrpc_endpoint(
        getdescriptions: bool = Query(default=False),
        getmetadata: bool = Query(default=False),
        filterbytransport: bool = Query(default=False),
        summary: bool = Query(default=True),
    ):
        result = await build_jsonrpc_tool().introspect_jsonrpc(
            getdescriptions=getdescriptions,
            getmetadata=getmetadata,
            filterbytransport=filterbytransport,
            summary=summary,
        )
        return result.to_dict()

    return app
