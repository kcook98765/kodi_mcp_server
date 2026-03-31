"""
Transport interface for Kodi MCP Server

Base class for all transport implementations
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

from ..models.messages import RequestMessage, ResponseMessage


class Transport(ABC):
    """Base transport interface for server-add-on communication"""

    @abstractmethod
    async def send_request(self, request: RequestMessage) -> ResponseMessage:
        """
        Send a request to the Kodi addon and receive the response.

        Args:
            request: RequestMessage to send to addon

        Returns:
            ResponseMessage from addon
        """
        pass

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to Kodi addon"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to Kodi addon"""
        pass
