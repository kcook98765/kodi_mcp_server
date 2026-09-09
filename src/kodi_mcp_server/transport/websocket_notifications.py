"""Experimental Kodi JSON-RPC WebSocket notification listener."""

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import websockets
from websockets.uri import parse_uri

from ..models.messages import ResponseMessage


class _TargetBoundEndpointNotConfigured(ValueError):
    """The probe lacks a usable endpoint authored for its target."""


_REDIRECT_STATUS_CODES = frozenset({300, 301, 302, 303, 307, 308})
_CROSS_ORIGIN_REDIRECT_ERROR = (
    "cross-origin redirect rejected for target-bound WebSocket endpoint"
)
_TARGET_BOUND_REDIRECT_ERROR = "WebSocket redirect failed for target-bound endpoint"
_MAX_EXCEPTION_GRAPH_NODES = 64


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

    def _target_bound_endpoint(self) -> tuple[str, str, int]:
        """Return an authored endpoint and its normalized effective origin."""
        authored_url = self.websocket_url
        if authored_url == "":
            if not isinstance(self.tcp_host, str):
                raise _TargetBoundEndpointNotConfigured(
                    "target WebSocket endpoint is not configured"
                )
            host = self.tcp_host.strip()
            if not host:
                raise _TargetBoundEndpointNotConfigured(
                    "target WebSocket endpoint is not configured"
                )
            if any(character.isspace() for character in host) or any(
                character in host for character in "/?#@[]"
            ):
                raise _TargetBoundEndpointNotConfigured(
                    "target WebSocket endpoint is not configured"
                )
            if ":" in host:
                try:
                    ipaddress.IPv6Address(host)
                except ValueError as exc:
                    raise _TargetBoundEndpointNotConfigured(
                        "target WebSocket endpoint is not configured"
                    ) from exc
                rendered_host = f"[{host}]"
            else:
                rendered_host = host
            if (
                isinstance(self.tcp_port, bool)
                or not isinstance(self.tcp_port, int)
                or not 1 <= self.tcp_port <= 65535
            ):
                raise _TargetBoundEndpointNotConfigured(
                    "target WebSocket endpoint is not configured"
                )
            websocket_url = f"ws://{rendered_host}:{self.tcp_port}/jsonrpc"
        else:
            if (
                not isinstance(authored_url, str)
                or not authored_url.strip()
                or authored_url != authored_url.strip()
                or any(character.isspace() for character in authored_url)
                or "\\" in authored_url
            ):
                raise _TargetBoundEndpointNotConfigured(
                    "target WebSocket endpoint is not configured"
                )
            websocket_url = authored_url

        try:
            raw_uri = urlsplit(websocket_url)
            raw_authority = raw_uri.netloc
            raw_host = raw_uri.hostname
            explicit_port = raw_uri.port
            parsed_uri = parse_uri(websocket_url)
            normalized_raw_host = (
                raw_host.encode("idna").decode().lower()
                if raw_host is not None
                else None
            )
        except (ValueError, UnicodeError, websockets.exceptions.InvalidURI) as exc:
            raise _TargetBoundEndpointNotConfigured(
                "target WebSocket endpoint is not configured"
            ) from exc
        dns_host = parsed_uri.host.rstrip(".")
        valid_dns_host = bool(dns_host) and len(parsed_uri.host) <= 253 and all(
            label
            and len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(character.isalnum() or character == "-" for character in label)
            for label in dns_host.split(".")
        )
        valid_ip_host = False
        if ":" in parsed_uri.host:
            try:
                ipaddress.IPv6Address(parsed_uri.host)
            except ValueError:
                pass
            else:
                valid_ip_host = True
        if (
            not raw_authority
            or raw_authority.endswith(":")
            or parsed_uri.username is not None
            or parsed_uri.password is not None
            or normalized_raw_host != parsed_uri.host
            or not (valid_dns_host or valid_ip_host)
            or explicit_port == 0
            or not 1 <= parsed_uri.port <= 65535
        ):
            raise _TargetBoundEndpointNotConfigured(
                "target WebSocket endpoint is not configured"
            )
        return websocket_url, parsed_uri.host, parsed_uri.port

    def _redirect_responses(
        self,
        exc: BaseException,
    ) -> tuple[tuple[tuple[str, ...], ...], bool]:
        """Collect redirect Location values from both exception-chain branches."""
        worklist: list[BaseException] = [exc]
        visited: set[int] = set()
        responses: list[tuple[str, ...]] = []
        while worklist and len(visited) < _MAX_EXCEPTION_GRAPH_NODES:
            current = worklist.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))
            if (
                isinstance(current, websockets.exceptions.InvalidStatus)
                and current.response.status_code in _REDIRECT_STATUS_CODES
            ):
                responses.append(tuple(current.response.headers.get_all("Location")))
            for related in (current.__cause__, current.__context__):
                if related is not None and id(related) not in visited:
                    worklist.append(related)
        return tuple(responses), not worklist

    def _redirect_crosses_authored_origin(
        self,
        authored_url: str,
        location: str,
    ) -> bool | None:
        """Compare normalized origins; return None for an unsafe redirect URI."""
        try:
            authored = parse_uri(authored_url)
            redirected = parse_uri(urljoin(authored_url, location))
        except Exception:
            return None
        if redirected.username is not None or redirected.password is not None:
            return None
        return (
            authored.secure,
            authored.host,
            authored.port,
        ) != (
            redirected.secure,
            redirected.host,
            redirected.port,
        )

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

    async def listen_target_bound(
        self,
        sample_size: int = 3,
        listen_seconds: int = 5,
    ) -> ResponseMessage:
        """Collect one sample without proxy discovery or cross-origin redirects."""
        try:
            ws_url, bound_host, bound_port = self._target_bound_endpoint()
        except _TargetBoundEndpointNotConfigured as exc:
            return ResponseMessage(
                request_id="websocket-notifications",
                result={
                    "connected": False,
                    "websocket_url": "<configured WebSocket endpoint>",
                    "messages": [],
                    "message_count": 0,
                    "listen_seconds": listen_seconds,
                    "event_trigger_used": None,
                    "diagnostic_code": "endpoint_not_configured",
                    "likely_cause": "target WebSocket endpoint is not configured",
                },
                error=str(exc),
            )

        display_url = self._display_websocket_url(ws_url)
        try:
            # websockets 17.1 documents proxy=None as disabling system proxy
            # discovery. Explicit host and port pin the TCP destination and
            # make its redirect policy reject every cross-origin redirect.
            async with websockets.connect(
                ws_url,
                open_timeout=self.timeout,
                proxy=None,
                host=bound_host,
                port=bound_port,
            ) as websocket:
                messages = []
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
                        continue

                return ResponseMessage(
                    request_id="websocket-notifications",
                    result={
                        "connected": True,
                        "websocket_url": display_url,
                        "messages": messages,
                        "message_count": len(messages),
                        "listen_seconds": listen_seconds,
                        "event_trigger_used": None,
                        "trigger_result": None,
                    },
                    error=None,
                )
        except Exception as exc:
            redirect_responses, graph_complete = self._redirect_responses(exc)
            redirect_origin_changes = [
                self._redirect_crosses_authored_origin(ws_url, locations[0])
                for locations in redirect_responses
                if len(locations) == 1
            ]
            cross_origin_redirect = True in redirect_origin_changes
            redirect_failure = bool(redirect_responses) or not graph_complete
            if cross_origin_redirect:
                error_text = _CROSS_ORIGIN_REDIRECT_ERROR
            elif redirect_failure:
                error_text = _TARGET_BOUND_REDIRECT_ERROR
            else:
                error_text = str(exc).replace(ws_url, display_url)
            return ResponseMessage(
                request_id="websocket-notifications",
                result={
                    "connected": False,
                    "websocket_url": display_url,
                    "messages": [],
                    "message_count": 0,
                    "listen_seconds": listen_seconds,
                    "event_trigger_used": None,
                    "diagnostic_code": (
                        "cross_origin_redirect"
                        if cross_origin_redirect
                        else (
                            "redirect_failure"
                            if redirect_failure
                            else self._diagnostic_code(exc)
                        )
                    ),
                    "likely_cause": (
                        _CROSS_ORIGIN_REDIRECT_ERROR
                        if cross_origin_redirect
                        else (
                            _TARGET_BOUND_REDIRECT_ERROR
                            if redirect_failure
                            else self._classify_error(exc)
                        )
                    ),
                },
                error=error_text,
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
