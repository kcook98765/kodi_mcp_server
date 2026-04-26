"""Tools for the minimal Kodi addon bridge."""

from ..models.messages import ResponseMessage
from ..transport.http_bridge import HttpBridgeClient

TEST_MARKERS = [
    "KODI_MCP_TEST_ADDON_EXECUTED_V2",
    "KODI_MCP_TEST_ADDON_EXECUTED",
]


class BridgeTool:
    """Developer-facing tools for the minimal Kodi addon bridge."""

    def __init__(self, client: HttpBridgeClient):
        self.client = client

    async def get_bridge_health(self) -> ResponseMessage:
        return await self.client.get_health()

    async def get_bridge_ping(self) -> ResponseMessage:
        return await self.client.get_ping()

    async def get_bridge_version(self) -> ResponseMessage:
        return await self.client.get_version()

    async def get_bridge_status(self) -> ResponseMessage:
        return await self.client.get_status()

    async def get_bridge_runtime_info(self) -> ResponseMessage:
        return await self.client.get_runtime_info()

    async def get_bridge_file(self, path: str) -> ResponseMessage:
        return await self.client.get_file(path=path)

    async def get_bridge_addon_info(self, addonid: str) -> ResponseMessage:
        return await self.client.get_addon_info(addonid=addonid)

    async def get_bridge_log_tail(self, lines: int = 20) -> ResponseMessage:
        return await self.client.get_log_tail(lines=lines)

    async def get_bridge_log_markers(self, lines: int = 100) -> ResponseMessage:
        return await self.client.get_log_markers(lines=lines)

    async def write_bridge_log_marker(self, message: str) -> ResponseMessage:
        return await self.client.write_log_marker(message=message)

    async def bridge_debug_ping(self) -> ResponseMessage:
        return await self.client.debug_ping()

    async def get_bridge_control_capabilities(self) -> ResponseMessage:
        return await self.client.get_control_capabilities()

    async def gui_action(self, action: str) -> ResponseMessage:
        return await self.client.gui_action(action=action)

    async def gui_screenshot(self, include_image: bool = False) -> ResponseMessage:
        return await self.client.gui_screenshot(include_image=include_image)

    async def ensure_bridge_addon_enabled(self, addonid: str) -> ResponseMessage:
        return await self.client.ensure_addon_enabled(addonid=addonid)

    async def execute_bridge_addon(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-execute-addon"
        
        # Check addon type before executing
        addon_info = await self.get_bridge_addon_info(addonid)
        if addon_info.error:
            return addon_info
        
        addon_type = addon_info.result.get("addon_type", "unknown")
        if addon_type == "service":
            from ..models.messages import ErrorType
            
            return ResponseMessage(
                request_id=request_id,
                result={
                    "addon_id": addonid,
                    "addon_type": "service",
                    "suggestion": "Use 'kodi-cli service status' to check status or 'kodi-cli service restart' to restart.",
                },
                error=f"Addon '{addonid}' is a service-type addon and cannot be manually executed. Service addons auto-start on install.",
                error_type=ErrorType.INVALID_OPERATION,
                error_code=400,
            )
        
        return await self.client.execute_addon(addonid=addonid)

    async def check_bridge_addon_version(self, addonid: str, expected_version: str) -> ResponseMessage:
        return await self.client.check_addon_version(addonid=addonid, expected_version=expected_version)

    async def execute_bridge_builtin(self, command: str, addonid: str | None = None) -> ResponseMessage:
        return await self.client.execute_builtin(command=command, addonid=addonid)

    async def trigger_repo_refresh(self) -> ResponseMessage:
        return await self.client.refresh_repo()

    async def upload_bridge_addon_zip(self, local_zip_path: str) -> ResponseMessage:
        return await self.client.upload_addon_zip(local_zip_path=local_zip_path)

    async def verify_bridge_addon_deploy(self, addonid: str, expected_version: str) -> ResponseMessage:
        request_id = "bridge-verify-addon-deploy"

        version_check = await self.check_bridge_addon_version(
            addonid=addonid,
            expected_version=expected_version,
        )
        execute_result = await self.execute_bridge_addon(addonid=addonid)

        import asyncio
        await asyncio.sleep(1)

        log_result = await self.get_bridge_log_tail(lines=100)

        actual_version = ((version_check.result or {}).get("actual_version"))
        version_matches = bool((version_check.result or {}).get("matches"))
        executed = bool((execute_result.result or {}).get("executed"))
        log_lines = ((log_result.result or {}).get("lines") or [])

        log_proof_found = False
        for line in log_lines:
            if addonid not in line:
                continue
            if actual_version and f"version={actual_version}" not in line:
                continue
            if any(marker in line for marker in TEST_MARKERS):
                log_proof_found = True
                break
            if "marker=" in line:
                log_proof_found = True
                break

        result = {
            "addon_id": addonid,
            "expected_version": expected_version,
            "actual_version": actual_version,
            "version_matches": version_matches,
            "executed": executed,
            "log_proof_found": log_proof_found,
            "ok": version_matches and executed and log_proof_found,
        }

        if version_check.error or execute_result.error or log_result.error:
            errors = [err for err in [version_check.error, execute_result.error, log_result.error] if err]
            return ResponseMessage(request_id=request_id, result=result, error=" | ".join(errors))

        return ResponseMessage(request_id=request_id, result=result, error=None)
