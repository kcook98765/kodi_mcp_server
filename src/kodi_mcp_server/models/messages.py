"""
Protocol message models for Kodi MCP Server

Structured request/response models for transport communication
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ErrorType(str, Enum):
    """Structured error classification for consistent error handling."""

    # Network and connection
    NETWORK_ERROR = "network_error"  # TCP connection failed, DNS failure

    # Request failures
    TIMEOUT = "timeout"              # Request timed out

    # Authentication
    AUTH_ERROR = "auth_error"        # 401/403 - credential issues

    # Resource issues
    NOT_FOUND = "not_found"          # 404 - resource not found

    # Server issues
    SERVER_ERROR = "server_error"    # 5xx - Kodi/server error

    # Data issues
    PARSE_ERROR = "parse_error"      # Invalid JSON response
    INVALID_RESPONSE = "invalid_response"  # Response schema mismatch

    # Configuration
    CONFIG_ERROR = "config_error"    # Missing required config

    # Fallback
    UNKNOWN_ERROR = "unknown_error"  # Unexpected errors


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
    error: Optional[str] = None
    error_type: Optional[ErrorType] = None
    error_code: Optional[int] = None
    latency_ms: Optional[int] = None  # Request latency in milliseconds

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResponseMessage":
        """Create from dictionary"""
        error_type_value = data.get("error_type")
        error_type = None
        if error_type_value:
            try:
                error_type = ErrorType(error_type_value)
            except ValueError:
                # Unknown error type - treat as unknown
                error_type = ErrorType.UNKNOWN_ERROR
        return cls(
            request_id=data["request_id"],
            result=data.get("result"),
            error=data.get("error"),
            error_type=error_type,
            error_code=data.get("error_code"),
            latency_ms=data.get("latency_ms"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "request_id": self.request_id,
            "result": self.result,
            "error": self.error,
            "error_type": self.error_type.value if self.error_type else None,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
        }
