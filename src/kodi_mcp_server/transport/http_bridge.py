"""HTTP client for the minimal Kodi addon bridge."""

import json
import socket
import time
from pathlib import Path
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from ..models.messages import ErrorType, ResponseMessage


def _http_code_to_error(code: int) -> ErrorType:
    """Map HTTP status code to ErrorType."""
    if code in (401, 403):
        return ErrorType.AUTH_ERROR
    elif code == 404:
        return ErrorType.NOT_FOUND
    elif 500 <= code < 600:
        return ErrorType.SERVER_ERROR
    else:
        return ErrorType.UNKNOWN_ERROR


def _url_error_to_response(exc: URLError, request_id: str) -> ResponseMessage:
    """Convert URLError to ResponseMessage with proper typing."""
    if isinstance(exc.reason, socket.timeout):
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error="request timeout",
            error_type=ErrorType.TIMEOUT,
        )
    else:
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error=f"connection error: {exc.reason}",
            error_type=ErrorType.NETWORK_ERROR,
        )


class HttpBridgeClient:
    """Minimal HTTP client for the Kodi addon bridge."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _retry_wrapper(self, method, *args, request_id: str, max_retries: int = 1, **kwargs) -> ResponseMessage:
        """Wrapper that retries on network errors (max 1 retry)."""
        start_time = time.time()
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = method(*args, **kwargs)  # method is sync, don't await
                return self._response(
                    request_id=request_id,
                    result=result.result,  # result is already ResponseMessage
                    error=result.error,
                    error_type=result.error_type,
                    error_code=result.error_code,
                    latency_ms=result.latency_ms,
                )
            except (URLError, socket.timeout) as exc:
                last_error = exc
                if attempt == max_retries:
                    # Final attempt failed, return error response
                    return _url_error_to_response(exc, request_id)
                # Retry: loop again

    def _response(
        self,
        request_id: str,
        result=None,
        error=None,
        error_type: ErrorType = None,
        error_code: int = None,
        latency_ms: int = None,
    ) -> ResponseMessage:
        return ResponseMessage(
            request_id=request_id,
            result=result,
            error=error,
            error_type=error_type,
            error_code=error_code,
            latency_ms=latency_ms,
        )

    def _make_request(self, method: str, path: str, query: dict | None = None, payload: dict | None = None) -> ResponseMessage:
        """Generic HTTP request with unified error handling and latency tracking."""
        url = self.base_url + path
        if query:
            url += "?" + urllib_parse.urlencode(query)

        headers = {"Content-Type": "application/json"} if payload else {}
        data = json.dumps(payload).encode("utf-8") if payload else None

        request = urllib_request.Request(url, data=data, headers=headers, method=method)

        start_time = time.time()

        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                latency_ms = int((time.time() - start_time) * 1000)
                return self._response(request_id="bridge-request", result=result, error=None, latency_ms=latency_ms)
        except HTTPError as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            return self._response(
                request_id="bridge-request",
                result=None,
                error=f"http error {exc.code}: {exc.reason}",
                error_type=_http_code_to_error(exc.code),
                error_code=exc.code,
                latency_ms=latency_ms,
            )
        except URLError as exc:
            return _url_error_to_response(exc, request_id="bridge-request")
        except socket.timeout:
            latency_ms = int((time.time() - start_time) * 1000)
            return self._response(
                request_id="bridge-request",
                result=None,
                error="request timeout",
                error_type=ErrorType.TIMEOUT,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            return self._response(
                request_id="bridge-request",
                result=None,
                error=f"request failed: {exc}",
                error_type=ErrorType.UNKNOWN_ERROR,
                latency_ms=latency_ms,
            )

    async def get_health(self) -> ResponseMessage:
        request_id = "bridge-health"
        return await self._retry_wrapper(
            self._make_request, "GET", "/health", request_id=request_id, max_retries=1
        )

    async def get_ping(self) -> ResponseMessage:
        request_id = "bridge-ping"
        return await self._retry_wrapper(
            self._make_request, "GET", "/ping", request_id=request_id, max_retries=1
        )

    async def get_version(self) -> ResponseMessage:
        request_id = "bridge-version"
        return await self._retry_wrapper(
            self._make_request, "GET", "/version", request_id=request_id, max_retries=1
        )

    async def get_status(self) -> ResponseMessage:
        request_id = "bridge-status"
        return await self._retry_wrapper(
            self._make_request, "GET", "/status", request_id=request_id, max_retries=1
        )

    async def get_runtime_info(self) -> ResponseMessage:
        request_id = "bridge-runtime-info"
        return await self._retry_wrapper(
            self._make_request, "GET", "/runtime/info", request_id=request_id, max_retries=1
        )

    async def get_file(self, path: str) -> ResponseMessage:
        request_id = "bridge-file-read"
        result = self._make_request("GET", "/files/read", query={"path": path})
        return self._response(request_id=request_id, result=result.result, error=result.error, error_type=result.error_type, error_code=result.error_code, latency_ms=result.latency_ms)

    async def get_addon_info(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-addon-info"
        return await self._retry_wrapper(
            self._make_request, "GET", "/addon/info", query={"addonid": addonid}, request_id=request_id, max_retries=1
        )

    async def get_log_tail(self, lines: int = 20) -> ResponseMessage:
        request_id = "bridge-log-tail"
        return await self._retry_wrapper(
            self._make_request, "GET", "/log/tail", query={"lines": lines}, request_id=request_id, max_retries=1
        )

    async def get_log_markers(self, lines: int = 100) -> ResponseMessage:
        request_id = "bridge-log-markers"
        return await self._retry_wrapper(
            self._make_request, "GET", "/log/markers", query={"lines": lines}, request_id=request_id, max_retries=1
        )

    async def write_log_marker(self, message: str) -> ResponseMessage:
        request_id = "bridge-log-marker"
        result = self._make_request("POST", "/log/marker", payload={"message": message})
        return self._response(request_id=request_id, result=result.result, error=result.error, error_type=result.error_type, error_code=result.error_code, latency_ms=result.latency_ms)

    async def debug_ping(self) -> ResponseMessage:
        request_id = "bridge-debug-ping"
        return await self._retry_wrapper(
            self._make_request, "POST", "/debug/ping", payload={}, request_id=request_id, max_retries=1
        )

    async def get_control_capabilities(self) -> ResponseMessage:
        request_id = "bridge-control-capabilities"
        return await self._retry_wrapper(
            self._make_request, "GET", "/control/capabilities", request_id=request_id, max_retries=1
        )

    async def ensure_addon_enabled(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-ensure-addon-enabled"
        result = self._make_request("POST", "/addon/ensure-enabled", query={"addonid": addonid}, payload={})
        return self._response(request_id=request_id, result=result.result, error=result.error, error_type=result.error_type, error_code=result.error_code, latency_ms=result.latency_ms)

    async def execute_addon(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-execute-addon"
        result = self._make_request("POST", "/addon/execute", query={"addonid": addonid}, payload={})
        return self._response(request_id=request_id, result=result.result, error=result.error, error_type=result.error_type, error_code=result.error_code, latency_ms=result.latency_ms)

    async def check_addon_version(self, addonid: str, expected_version: str) -> ResponseMessage:
        request_id = "bridge-addon-version-check"
        result = self._make_request("GET", "/addon/version-check", query={"addonid": addonid, "expected_version": expected_version})
        return self._response(request_id=request_id, result=result.result, error=result.error, error_type=result.error_type, error_code=result.error_code, latency_ms=result.latency_ms)

    async def execute_builtin(self, command: str, addonid: str | None = None) -> ResponseMessage:
        request_id = "bridge-execute-builtin"
        query = {"command": command}
        if addonid:
            query["addonid"] = addonid
        result = self._make_request("POST", "/execute_builtin", query=query, payload={})
        return self._response(request_id=request_id, result=result.result, error=result.error, error_type=result.error_type, error_code=result.error_code, latency_ms=result.latency_ms)

    async def upload_addon_zip(self, local_zip_path: str) -> ResponseMessage:
        request_id = "bridge-addon-upload"
        try:
            zip_path = Path(local_zip_path)
            body = zip_path.read_bytes()
            url = self.base_url + "/addon/upload?" + urllib_parse.urlencode({"filename": zip_path.name})
            request = urllib_request.Request(url, data=body, headers={"Content-Type": "application/zip"}, method="POST")

            start_time = time.time()
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                latency_ms = int((time.time() - start_time) * 1000)
                return self._response(request_id=request_id, result=result, error=None, latency_ms=latency_ms)
        except FileNotFoundError:
            return self._response(request_id=request_id, result=None, error=f"local file not found: {local_zip_path}", error_type=ErrorType.UNKNOWN_ERROR)
        except HTTPError as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            return self._response(
                request_id=request_id,
                result=None,
                error=f"http error {exc.code}: {exc.reason}",
                error_type=_http_code_to_error(exc.code),
                error_code=exc.code,
                latency_ms=latency_ms,
            )
        except URLError as exc:
            return _url_error_to_response(exc, request_id)
        except socket.timeout:
            latency_ms = int((time.time() - start_time) * 1000)
            return self._response(
                request_id=request_id,
                result=None,
                error="request timeout",
                error_type=ErrorType.TIMEOUT,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            return self._response(
                request_id=request_id,
                result=None,
                error=f"request failed: {exc}",
                error_type=ErrorType.UNKNOWN_ERROR,
                latency_ms=latency_ms,
            )
