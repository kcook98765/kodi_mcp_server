"""
Mock transport for Kodi MCP Server.

Test-only in-memory transport for local vertical-slice testing.
"""

from ..models.messages import RequestMessage, ResponseMessage
from ..transport.base import Transport


class MockTransport(Transport):
    """Minimal test-only transport implementation."""

    async def connect(self) -> None:
        """No-op connect for local testing."""
        return None

    async def disconnect(self) -> None:
        """No-op disconnect for local testing."""
        return None

    async def send_request(self, request: RequestMessage) -> ResponseMessage:
        """Return a canned response for supported test commands."""
        if request.command == "get_kodi_log_tail":
            return ResponseMessage(
                request_id=request.request_id,
                result={
                    "lines": [
                        "INFO mock kodi log line 1",
                        "WARNING mock kodi log line 2",
                        "ERROR mock kodi log line 3",
                    ]
                },
                error=None,
            )

        if request.command == "execute_jsonrpc":
            return ResponseMessage(
                request_id=request.request_id,
                result={
                    "method": request.args.get("method"),
                    "params": request.args.get("params"),
                    "status": "ok",
                    "mock": True,
                },
                error=None,
            )

        return ResponseMessage(
            request_id=request.request_id,
            result=None,
            error="unknown command",
        )
