"""
Service addon lifecycle operations.

Provides service-specific operations for service-type addons.
Slice 1a: Status only (restart deferred to later slice).
"""

from ..models.messages import ResponseMessage
from ..transport.http_bridge import HttpBridgeClient


class ServiceOpsTool:
    """Service addon lifecycle operations."""
    
    def __init__(self, bridge_client: HttpBridgeClient):
        self.bridge = bridge_client
    
    async def get_service_status(self, addonid: str) -> ResponseMessage:
        """Get service addon status (metadata-based).
        
        NOTE: This is metadata status only. True runtime liveness
        detection would require custom Kodi RPC endpoints which we
        don't have for service.kodi_mcp yet.
        """
        request_id = "service-status"
        
        # Get addon info (includes type and enabled status)
        addon_info = await self.bridge.get_bridge_addon_info(addonid)
        if addon_info.error:
            return ResponseMessage(
                request_id=request_id,
                result=None,
                error=addon_info.error,
                error_type=addon_info.error_type,
                error_code=addon_info.error_code,
            )
        
        result = {
            "addon_id": addonid,
            "addon_type": addon_info.result.get("addon_type", "unknown"),
            "enabled": addon_info.result.get("enabled", False),
            "version": addon_info.result.get("version"),
            "path": addon_info.result.get("install_path"),
            "note": "Enabled status is metadata. True runtime liveness not guaranteed without custom RPC endpoints.",
        }
        
        return ResponseMessage(
            request_id=request_id,
            result=result,
            error=None,
            error_type=None,
            error_code=None,
        )
