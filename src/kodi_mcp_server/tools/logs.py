"""
Log tools for Kodi MCP Server

Tools for retrieving and managing Kodi logs
"""

import uuid
from typing import Optional

from ..models.messages import RequestMessage, ResponseMessage
from ..transport.base import Transport


class LogTool:
    """Tool for retrieving Kodi logs"""

    def __init__(self, transport: Optional[Transport]):
        """
        Initialize LogTool with a transport instance.

        Args:
            transport: Transport instance for communication with Kodi addon
        """
        self.transport = transport

    async def get_kodi_log_tail(self, limit: int = 100) -> ResponseMessage:
        """
        Retrieve the last N lines from Kodi's log file and return a structured response.

        Args:
            limit: Number of log lines to retrieve (default: 100)

        Returns:
            ResponseMessage containing either the tool result or a placeholder error
            when transport is not implemented.
        """
        request = RequestMessage(
            request_id=str(uuid.uuid4()),
            command="get_kodi_log_tail",
            args={"limit": limit},
        )

        if self.transport is None:
            return ResponseMessage(
                request_id=request.request_id,
                result=None,
                error="transport not implemented",
            )

        return await self.transport.send_request(request)
