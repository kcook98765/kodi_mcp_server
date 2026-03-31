"""
Protocol message models for Kodi MCP Server

Structured request/response models for transport communication
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class RequestMessage:
    """Request message sent to Kodi addon"""

    request_id: str
    command: str
    args: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "request_id": self.request_id,
            "command": self.command,
            "args": self.args,
        }


@dataclass
class ResponseMessage:
    """Response message from Kodi addon"""

    request_id: str
    result: Optional[Dict[str, Any]]
    error: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResponseMessage":
        """Create from dictionary"""
        return cls(
            request_id=data["request_id"],
            result=data.get("result"),
            error=data.get("error"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "request_id": self.request_id,
            "result": self.result,
            "error": self.error,
        }
