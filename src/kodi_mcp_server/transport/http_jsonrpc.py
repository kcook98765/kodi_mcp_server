"""
HTTP JSON-RPC transport for Kodi MCP Server.

Minimal real transport for executing Kodi JSON-RPC commands over HTTP.
"""

import base64
import json
import socket
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from ..models.messages import RequestMessage, ResponseMessage
from ..transport.base import Transport


class HttpJsonRpcTransport(Transport):
    """Minimal HTTP transport for Kodi JSON-RPC execution."""

    def __init__(self, url: str, username: str, password: str, timeout: int = 10):
        self.url = url
        self.username = username
        self.password = password
        self.timeout = timeout

    async def connect(self) -> None:
        """No-op connect for stateless HTTP transport."""
        return None

    async def disconnect(self) -> None:
        """No-op disconnect for stateless HTTP transport."""
        return None

    def _error_response(self, request_id: str, message: str) -> ResponseMessage:
        """Build a consistent error response."""
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error=message,
        )

    async def send_request(self, request: RequestMessage) -> ResponseMessage:
        """Send supported requests to Kodi over HTTP JSON-RPC."""
        if request.command != "execute_jsonrpc":
            return self._error_response(
                request.request_id,
                "unsupported command for http transport",
            )

        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request.request_id,
                "method": request.args.get("method"),
                "params": request.args.get("params", {}),
            }
        ).encode("utf-8")

        credentials = f"{self.username}:{self.password}".encode("utf-8")
        auth_header = base64.b64encode(credentials).decode("ascii")

        http_request = urllib_request.Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth_header}",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(http_request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            return self._error_response(
                request.request_id,
                f"http error {exc.code}: {exc.reason}",
            )
        except socket.timeout:
            return self._error_response(request.request_id, "request timeout")
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout):
                return self._error_response(request.request_id, "request timeout")
            return self._error_response(
                request.request_id,
                f"connection error: {reason}",
            )
        except Exception as exc:
            return self._error_response(
                request.request_id,
                f"request failed: {exc}",
            )

        try:
            response_data = json.loads(raw_body)
        except json.JSONDecodeError:
            return self._error_response(
                request.request_id,
                "invalid json response from kodi",
            )

        if response_data.get("error") is not None:
            error = response_data.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                error_text = f"jsonrpc error {code}: {message}"
            else:
                error_text = str(error)
            return self._error_response(request.request_id, error_text)

        return ResponseMessage(
            request_id=request.request_id,
            result=response_data.get("result"),
            error=None,
        )
