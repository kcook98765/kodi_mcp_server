"""HTTP client for the minimal Kodi addon bridge."""

import json
import socket
from pathlib import Path
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from ..models.messages import ErrorType, ResponseMessage


class HttpBridgeClient:
    """Minimal HTTP client for the Kodi addon bridge."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _retry_wrapper(self, method, *args, request_id: str, max_retries: int = 1, **kwargs) -> ResponseMessage:
        """Wrapper that retries on network errors (max 1 retry)."""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await method(*args, **kwargs)
            except (URLError, socket.timeout) as exc:
                last_error = exc
                if attempt == max_retries:
                    # Final attempt failed, return error response
                    if isinstance(exc.reason, socket.timeout):
                        return self._response(
                            request_id=request_id,
                            result=None,
                            error="request timeout",
                            error_type=ErrorType.TIMEOUT,
                        )
                    else:
                        return self._response(
                            request_id=request_id,
                            result=None,
                            error=f"connection error: {exc.reason}",
                            error_type=ErrorType.NETWORK_ERROR,
                        )
                # Retry: loop again

    def _response(
        self,
        request_id: str,
        result=None,
        error=None,
        error_type: ErrorType = None,
        error_code: int = None,
    ) -> ResponseMessage:
        return ResponseMessage(
            request_id=request_id,
            result=result,
            error=error,
            error_type=error_type,
            error_code=error_code,
        )

    def _get_json(self, path: str, query: dict | None = None):
        url = self.base_url + path
        if query:
            url += "?" + urllib_parse.urlencode(query)
        request = urllib_request.Request(url, method="GET")
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403):
                error_type = ErrorType.AUTH_ERROR
            elif exc.code == 404:
                error_type = ErrorType.NOT_FOUND
            elif 500 <= exc.code < 600:
                error_type = ErrorType.SERVER_ERROR
            else:
                error_type = ErrorType.UNKNOWN_ERROR
            raise exc  # Re-raise for caller to handle
        except socket.timeout:
            raise URLError("timeout")

    def _post_json(self, path: str, payload: dict):
        url = self.base_url + path
        request = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_bytes(self, path: str, body: bytes, content_type: str = "application/octet-stream"):
        url = self.base_url + path
        request = urllib_request.Request(
            url,
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    async def get_health(self) -> ResponseMessage:
        request_id = "bridge-health"
        return await self._retry_wrapper(
            self._get_json, "/health", request_id=request_id, max_retries=1
        )

    async def get_status(self) -> ResponseMessage:
        request_id = "bridge-status"
        return await self._retry_wrapper(
            self._get_json, "/status", request_id=request_id, max_retries=1
        )

    async def get_runtime_info(self) -> ResponseMessage:
        request_id = "bridge-runtime-info"
        return await self._retry_wrapper(
            self._get_json, "/runtime/info", request_id=request_id, max_retries=1
        )

    async def get_file(self, path: str) -> ResponseMessage:
        request_id = "bridge-file-read"
        try:
            payload = self._get_json("/files/read", query={"path": path})
            return self._response(request_id=request_id, result=payload, error=None)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)

    async def get_addon_info(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-addon-info"
        return await self._retry_wrapper(
            self._get_json, "/addon/info", query={"addonid": addonid}, request_id=request_id, max_retries=1
        )

    async def get_log_tail(self, lines: int = 20) -> ResponseMessage:
        request_id = "bridge-log-tail"
        return await self._retry_wrapper(
            self._get_json, "/log/tail", query={"lines": lines}, request_id=request_id, max_retries=1
        )

    def _handle_http_error(self, request_id: str, exc: HTTPError) -> ResponseMessage:
        """Map HTTP errors to typed responses."""
        if exc.code in (401, 403):
            error_type = ErrorType.AUTH_ERROR
        elif exc.code == 404:
            error_type = ErrorType.NOT_FOUND
        elif 500 <= exc.code < 600:
            error_type = ErrorType.SERVER_ERROR
        else:
            error_type = ErrorType.UNKNOWN_ERROR
        return self._response(
            request_id=request_id,
            result=None,
            error=f"http error {exc.code}: {exc.reason}",
            error_type=error_type,
            error_code=exc.code,
        )

    def _handle_url_error(self, request_id: str, exc: URLError) -> ResponseMessage:
        """Map URL errors to typed responses."""
        if isinstance(exc.reason, socket.timeout):
            error_type = ErrorType.TIMEOUT
        else:
            error_type = ErrorType.NETWORK_ERROR
        return self._response(
            request_id=request_id,
            result=None,
            error=f"connection error: {exc.reason}",
            error_type=error_type,
        )

    async def get_log_markers(self, lines: int = 100) -> ResponseMessage:
        request_id = "bridge-log-markers"
        return await self._retry_wrapper(
            self._get_json, "/log/markers", query={"lines": lines}, request_id=request_id, max_retries=1
        )

    async def write_log_marker(self, message: str) -> ResponseMessage:
        request_id = "bridge-log-marker"
        try:
            payload = self._post_json("/log/marker", payload={"message": message})
            return self._response(request_id=request_id, result=payload, error=None)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)

    async def debug_ping(self) -> ResponseMessage:
        request_id = "bridge-debug-ping"
        return await self._retry_wrapper(
            self._post_json, "/debug/ping", payload={}, request_id=request_id, max_retries=1
        )

    async def ensure_addon_enabled(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-ensure-addon-enabled"
        try:
            payload = self._post_json("/addon/ensure-enabled?" + urllib_parse.urlencode({"addonid": addonid}), payload={})
            return self._response(request_id=request_id, result=payload, error=None)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)

    async def execute_addon(self, addonid: str) -> ResponseMessage:
        request_id = "bridge-execute-addon"
        try:
            payload = self._post_json("/addon/execute?" + urllib_parse.urlencode({"addonid": addonid}), payload={})
            return self._response(request_id=request_id, result=payload, error=None)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)

    async def check_addon_version(self, addonid: str, expected_version: str) -> ResponseMessage:
        request_id = "bridge-addon-version-check"
        try:
            payload = self._get_json(
                "/addon/version-check",
                query={"addonid": addonid, "expected_version": expected_version},
            )
            return self._response(request_id=request_id, result=payload, error=None)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)

    async def execute_builtin(self, command: str, addonid: str | None = None) -> ResponseMessage:
        request_id = "bridge-execute-builtin"
        try:
            query = {"command": command}
            if addonid:
                query["addonid"] = addonid
            payload = self._post_json("/execute_builtin?" + urllib_parse.urlencode(query), payload={})
            return self._response(request_id=request_id, result=payload, error=None)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)

    async def upload_addon_zip(self, local_zip_path: str) -> ResponseMessage:
        request_id = "bridge-addon-upload"
        try:
            zip_path = Path(local_zip_path)
            body = zip_path.read_bytes()
            payload = self._post_bytes(
                "/addon/upload?" + urllib_parse.urlencode({"filename": zip_path.name}),
                body=body,
                content_type="application/zip",
            )
            return self._response(request_id=request_id, result=payload, error=None)
        except FileNotFoundError:
            return self._response(request_id=request_id, result=None, error=f"local file not found: {local_zip_path}", error_type=ErrorType.UNKNOWN_ERROR)
        except HTTPError as exc:
            return self._handle_http_error(request_id, exc)
        except URLError as exc:
            return self._handle_url_error(request_id, exc)
        except socket.timeout:
            return self._response(request_id=request_id, result=None, error="request timeout", error_type=ErrorType.TIMEOUT)
        except Exception as exc:
            return self._response(request_id=request_id, result=None, error=f"request failed: {exc}", error_type=ErrorType.UNKNOWN_ERROR)
