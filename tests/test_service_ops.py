"""
Tests for service addon lifecycle operations (Slice 1a).

Verifies service-type addon detection and status retrieval.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from kodi_mcp_server.tools.service_ops import ServiceOpsTool
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient
from kodi_mcp_server.models.messages import ResponseMessage


class TestServiceOps:
    """Test service addon lifecycle operations."""
    
    @pytest.fixture
    def mock_bridge_client(self):
        """Create mock bridge client."""
        client = MagicMock(spec=HttpBridgeClient)
        client.get_bridge_addon_info = AsyncMock(return_value=ResponseMessage(
            request_id="bridge-addon-info",
            result={
                "addon_type": "service",
                "enabled": True,
                "version": "0.2.16",
                "install_path": "/path/to/addon",
            },
            error=None,
            error_type=None,
        ))
        return client
    
    @pytest.fixture
    def service_ops(self, mock_bridge_client):
        """Create ServiceOpsTool instance."""
        return ServiceOpsTool(bridge_client=mock_bridge_client)
    
    @pytest.mark.asyncio
    async def test_get_service_status(self, service_ops):
        """get_service_status returns correct metadata."""
        result = await service_ops.get_service_status("service.kodi_mcp")
        
        assert result.error is None
        assert result.result is not None
        assert result.result["addon_id"] == "service.kodi_mcp"
        assert result.result["addon_type"] == "service"
        assert result.result["enabled"] is True
        assert result.result["version"] == "0.2.16"
        assert result.result["path"] == "/path/to/addon"
        assert "note" in result.result  # Warns about metadata limitation
    
    @pytest.mark.asyncio
    async def test_get_service_status_not_found(self, service_ops, mock_bridge_client):
        """get_service_status returns error when addon not found."""
        mock_bridge_client.get_bridge_addon_info = AsyncMock(return_value=ResponseMessage(
            request_id="bridge-addon-info",
            result=None,
            error="addon not found",
            error_type="not_found",
            error_code=404,
        ))
        
        result = await service_ops.get_service_status("unknown.addon")
        
        assert result.error == "addon not found"
        assert result.error_type == "not_found"
        assert result.error_code == 404


class TestBridgeAddonExecute:
    """Test addon execute service-type detection."""
    
    @pytest.fixture
    def bridge_tool(self):
        """Create bridge tool with mock client."""
        from kodi_mcp_server.tools.bridge import BridgeTool
        from kodi_mcp_server.transport.http_bridge import HttpBridgeClient
        
        client = MagicMock(spec=HttpBridgeClient)
        
        def get_execute_addon_side_effect(addonid):
            return ResponseMessage(
                request_id="bridge-execute-addon",
                result={"executed": True},
                error=None,
                error_type=None,
                error_code=None,
            )
        
        client.execute_addon = AsyncMock(side_effect=get_execute_addon_side_effect)
        
        return BridgeTool(client=client)
    
    @pytest.mark.asyncio
    async def test_execute_service_addon_returns_error(self, bridge_tool):
        """addon execute on service type returns INVALID_OPERATION error."""
        # Setup: addon info returns service type
        bridge_tool.client.get_addon_info = AsyncMock(return_value=ResponseMessage(
            request_id="bridge-addon-info",
            result={
                "addon_type": "service",
                "enabled": True,
                "version": "0.2.16",
            },
            error=None,
            error_type=None,
        ))
        
        result = await bridge_tool.execute_bridge_addon("service.kodi_mcp")
        
        # Should NOT call execute_addon
        bridge_tool.client.execute_addon.assert_not_called()
        
        # Should return invalid_operation error (top-level)
        assert result.error is not None
        assert "service-type addon" in result.error
        assert "cannot be manually executed" in result.error
        assert result.error_type == "invalid_operation"
        assert result.error_code == 400
        
        # Result should contain addon_type and suggestion (not error)
        assert result.result is not None
        assert result.result["addon_id"] == "service.kodi_mcp"
        assert result.result["addon_type"] == "service"
        assert "suggestion" in result.result
    
    @pytest.mark.asyncio
    async def test_execute_script_addon_proceeds(self, bridge_tool):
        """addon execute on script type proceeds normally."""
        # Setup: addon info returns script type
        bridge_tool.client.get_addon_info = AsyncMock(return_value=ResponseMessage(
            request_id="bridge-addon-info",
            result={
                "addon_type": "script",
                "enabled": True,
                "version": "1.0.0",
            },
            error=None,
            error_type=None,
        ))
        
        result = await bridge_tool.execute_bridge_addon("script.addon")
        
        # Should call execute_addon
        bridge_tool.client.execute_addon.assert_called_once_with(addonid="script.addon")
        
        # Should return success
        assert result.error is None
        assert result.result == {"executed": True}
    
    @pytest.mark.asyncio
    async def test_execute_with_info_error_returns_error(self, bridge_tool):
        """addon execute returns error when addon_info fails."""
        # Setup: addon info returns error
        bridge_tool.client.get_addon_info = AsyncMock(return_value=ResponseMessage(
            request_id="bridge-addon-info",
            result=None,
            error="addon not found",
            error_type="not_found",
            error_code=404,
        ))
        
        result = await bridge_tool.execute_bridge_addon("unknown.addon")
        
        # Should NOT call execute_addon
        bridge_tool.client.execute_addon.assert_not_called()
        
        # Should return the error from addon_info
        assert result.error == "addon not found"
        assert result.error_type == "not_found"
        assert result.error_code == 404
