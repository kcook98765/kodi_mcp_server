"""Experimental Kodi JSON-RPC WebSocket notification listener."""

import asyncio
import json
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import websockets

from ..models.messages import ResponseMessage


class WebSocketNotificationProbe:
    """Minimal separate probe for Kodi WebSocket notifications."""

    def __init__(
        self,
        tcp_host: str,
        tcp_port: int = 9090,
        websocket_url: str = "",
        timeout: int = 10,
    ):
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.websocket_url = websocket_url
        self.timeout = timeout

    def _websocket_url(self) -> str:
        """Build the WebSocket endpoint from explicit config or TCP host/port."""
        if self.websocket_url:
            return self.websocket_url
        return f"ws://{self.tcp_host}:{self.tcp_port}/jsonrpc"

    def _display_websocket_url(self, websocket_url: str) -> str:
        """Remove credentials, query, and fragment from an endpoint diagnostic."""
        try:
            parsed = urlsplit(websocket_url)
            hostname = parsed.hostname
            if not parsed.scheme or not hostname:
                return "<configured WebSocket endpoint>"
            display_host = f"[{hostname}]" if ":" in hostname else hostname
            port = parsed.port
            netloc = f"{display_host}:{port}" if port is not None else display_host
            return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        except ValueError:
            return "<configured WebSocket endpoint>"

    def _classify_error(self, exc: BaseException) -> str:
        """Return a likely failure cause for the current error."""
        # Timeout and DNS failures carry no stable distinguishing text
        # (``str(TimeoutError())`` is empty; ``gaierror`` text is
        # platform-dependent), so classify them by type; everything else
        # falls back to message text.
        if isinstance(exc, TimeoutError):
            return "connection to Kodi timed out"
        if isinstance(exc, socket.gaierror):
            return "DNS/name resolution failure"
        if isinstance(exc, ConnectionRefusedError):
            return "Kodi TCP control not enabled or wrong TCP port"
        if isinstance(exc, websockets.exceptions.ConnectionClosed):
            return (
                "WebSocket connection was interrupted after connecting; a route/target "
                "change, endpoint restart, or network interruption may have occurred. "
                "Retry with a fresh sample and verify current health"
            )
        if isinstance(exc, websockets.exceptions.InvalidStatus):
            status_code = exc.response.status_code
            if status_code in (401, 403):
                return "auth/handshake mismatch"
            return f"WebSocket handshake failed (HTTP {status_code})"
        if isinstance(exc, websockets.exceptions.InvalidHandshake):
            return "WebSocket handshake failed"
        lowered = str(exc).lower()
        if "connection refused" in lowered:
            return "Kodi TCP control not enabled or wrong TCP port"
        if "401" in lowered or "403" in lowered or "unauthorized" in lowered:
            return "auth/handshake mismatch"
        if "invalidstatus" in lowered or "http 200" in lowered:
            return "auth/handshake mismatch"
        if isinstance(exc, OSError):
            return "WebSocket network transport failed; verify endpoint and route availability"
        return "WebSocket transport failed for an unknown reason; verify current endpoint health"

    def _diagnostic_code(self, exc: BaseException) -> str:
        """Return a stable machine-readable category for a transport failure."""
        if isinstance(exc, TimeoutError):
            return "connection_timeout"
        if isinstance(exc, socket.gaierror):
            return "name_resolution_failure"
        if isinstance(exc, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(exc, OSError):
            return "network_failure"
        if isinstance(exc, websockets.exceptions.ConnectionClosed):
            return "connection_interrupted"
        if isinstance(exc, websockets.exceptions.InvalidStatus):
            if exc.response.status_code in (401, 403):
                return "handshake_auth_failure"
            return "handshake_failure"
        if isinstance(exc, websockets.exceptions.InvalidHandshake):
            return "handshake_failure"
        return "transport_failure"

    async def listen(self, sample_size: int = 3, listen_seconds: int = 5) -> ResponseMessage:
        """Connect and collect a small sample of WebSocket messages."""
        return await self.listen_with_trigger(
            sample_size=sample_size,
            listen_seconds=listen_seconds,
            trigger=None,
            trigger_name=None,
        )

    async def listen_with_trigger(
        self,
        sample_size: int = 3,
        listen_seconds: int = 5,
        trigger: Callable[[], Awaitable[ResponseMessage]] | None = None,
        trigger_name: str | None = None,
    ) -> ResponseMessage:
        """Connect, optionally trigger an event, and collect a message sample."""
        ws_url = self._websocket_url()
        display_url = self._display_websocket_url(ws_url)
        try:
            async with websockets.connect(ws_url, open_timeout=self.timeout) as websocket:
                messages = []
                trigger_response = None
                if trigger is not None:
                    await asyncio.sleep(1)
                    trigger_response = await trigger()

                deadline = asyncio.get_running_loop().time() + listen_seconds
                while len(messages) < sample_size:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    try:
                        messages.append(json.loads(message))
                    except json.JSONDecodeError:
                        # Skip frames that are not valid JSON: a parse
                        # problem on one frame is not a WebSocket
                        # connection failure, so collection continues.
                        continue

                return ResponseMessage(
                    request_id="websocket-notifications",
                    result={
                        "connected": True,
                        "websocket_url": display_url,
                        "messages": messages,
                        "message_count": len(messages),
                        "listen_seconds": listen_seconds,
                        "event_trigger_used": trigger_name,
                        "trigger_result": None if trigger_response is None else {
                            "result": trigger_response.result,
                            "error": trigger_response.error,
                        },
                    },
                    error=None,
                )
        except Exception as exc:
            error_text = str(exc).replace(ws_url, display_url)
            return ResponseMessage(
                request_id="websocket-notifications",
                result={
                    "connected": False,
                    "websocket_url": display_url,
                    "messages": [],
                    "message_count": 0,
                    "listen_seconds": listen_seconds,
                    "event_trigger_used": trigger_name,
                    "diagnostic_code": self._diagnostic_code(exc),
                    "likely_cause": self._classify_error(exc),
                },
                error=error_text,
            )
