"""Experimental Kodi JSON-RPC WebSocket notification listener."""

import asyncio
import json
from collections.abc import Awaitable, Callable

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

    def _classify_error(self, error_text: str) -> str:
        """Return a likely failure cause for the current error."""
        lowered = error_text.lower()
        if "connection refused" in lowered or "timed out" in lowered:
            return "Kodi TCP control not enabled or wrong TCP port"
        if "401" in lowered or "403" in lowered or "unauthorized" in lowered:
            return "auth/handshake mismatch"
        if "invalidstatus" in lowered or "http 200" in lowered:
            return "auth/handshake mismatch"
        return "Kodi TCP control not enabled or wrong TCP port"

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
                    messages.append(json.loads(message))

                return ResponseMessage(
                    request_id="websocket-notifications",
                    result={
                        "connected": True,
                        "websocket_url": ws_url,
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
            error_text = str(exc)
            return ResponseMessage(
                request_id="websocket-notifications",
                result={
                    "connected": False,
                    "websocket_url": ws_url,
                    "messages": [],
                    "message_count": 0,
                    "listen_seconds": listen_seconds,
                    "event_trigger_used": trigger_name,
                    "likely_cause": self._classify_error(error_text),
                },
                error=error_text,
            )
