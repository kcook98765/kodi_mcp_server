"""Tests for HTTP error mapping in HttpJsonRpcTransport."""
import sys
import socket
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from unittest.mock import patch, MagicMock

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport, is_safe_to_retry
from kodi_mcp_server.models.messages import RequestMessage


def test_http_error_401_mapped_to_auth_error():
    """HTTP 401/403 mapped to AUTH_ERROR."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    # Mock URLError to raise HTTP 401
    mock_fp = MagicMock()
    http_error = HTTPError(
        url="http://test:8080/jsonrpc",
        code=401,
        hdrs={},
        fp=mock_fp,
        msg="Unauthorized",
    )

    with patch("kodi_mcp_server.transport.http_jsonrpc.urllib_request.Request"):
        with patch("kodi_mcp_server.transport.http_jsonrpc.urllib_request.urlopen", side_effect=http_error):
            request = RequestMessage(
                request_id="test-401",
                command="execute_jsonrpc",
                args={"method": "Test", "params": {}},
            )

            import asyncio

            response = asyncio.get_event_loop().run_until_complete(
                transport.send_request(request)
            )

            assert response.error_type == ErrorType.AUTH_ERROR
            assert "401" in response.error


def test_http_error_403_mapped_to_auth_error():
    """HTTP 403 mapped to AUTH_ERROR."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    mock_fp = MagicMock()
    http_error = HTTPError(
        url="http://test:8080/jsonrpc",
        code=403,
        hdrs={},
        fp=mock_fp,
        msg="Forbidden",
    )

    with patch("kodi_mcp_server.transport.http_jsonrpc.urllib_request.Request"):
        with patch(
            "kodi_mcp_server.transport.http_jsonrpc.urllib_request.urlopen",
            side_effect=http_error,
        ):
            request = RequestMessage(
                request_id="test-403",
                command="execute_jsonrpc",
                args={"method": "Test", "params": {}},
            )

            import asyncio

            response = asyncio.get_event_loop().run_until_complete(
                transport.send_request(request)
            )

            assert response.error_type == ErrorType.AUTH_ERROR


def test_http_error_404_mapped_to_not_found():
    """HTTP 404 mapped to NOT_FOUND."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    mock_fp = MagicMock()
    http_error = HTTPError(
        url="http://test:8080/jsonrpc",
        code=404,
        hdrs={},
        fp=mock_fp,
        msg="Not Found",
    )

    with patch("kodi_mcp_server.transport.http_jsonrpc.urllib_request.Request"):
        with patch(
            "kodi_mcp_server.transport.http_jsonrpc.urllib_request.urlopen",
            side_effect=http_error,
        ):
            request = RequestMessage(
                request_id="test-404",
                command="execute_jsonrpc",
                args={"method": "Test", "params": {}},
            )

            import asyncio

            response = asyncio.get_event_loop().run_until_complete(
                transport.send_request(request)
            )

            assert response.error_type == ErrorType.NOT_FOUND


def test_http_error_500_mapped_to_server_error():
    """HTTP 5xx mapped to SERVER_ERROR."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    mock_fp = MagicMock()
    http_error = HTTPError(
        url="http://test:8080/jsonrpc",
        code=500,
        hdrs={},
        fp=mock_fp,
        msg="Internal Server Error",
    )

    with patch("kodi_mcp_server.transport.http_jsonrpc.urllib_request.Request"):
        with patch(
            "kodi_mcp_server.transport.http_jsonrpc.urllib_request.urlopen",
            side_effect=http_error,
        ):
            request = RequestMessage(
                request_id="test-500",
                command="execute_jsonrpc",
                args={"method": "Test", "params": {}},
            )

            import asyncio

            response = asyncio.get_event_loop().run_until_complete(
                transport.send_request(request)
            )

            assert response.error_type == ErrorType.SERVER_ERROR
            assert response.error_code == 500


def test_http_error_503_mapped_to_server_error():
    """HTTP 503 mapped to SERVER_ERROR."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    mock_fp = MagicMock()
    http_error = HTTPError(
        url="http://test:8080/jsonrpc",
        code=503,
        hdrs={},
        fp=mock_fp,
        msg="Service Unavailable",
    )

    with patch("kodi_mcp_server.transport.http_jsonrpc.urllib_request.Request"):
        with patch(
            "kodi_mcp_server.transport.http_jsonrpc.urllib_request.urlopen",
            side_effect=http_error,
        ):
            request = RequestMessage(
                request_id="test-503",
                command="execute_jsonrpc",
                args={"method": "Test", "params": {}},
            )

            import asyncio

            response = asyncio.get_event_loop().run_until_complete(
                transport.send_request(request)
            )

            assert response.error_type == ErrorType.SERVER_ERROR
            assert response.error_code == 503


def test_error_response_has_error_code():
    """Error responses include error_code when available."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    error_response = transport._error_response(
        request_id="test-code",
        message="test error",
        error_type=ErrorType.SERVER_ERROR,
        error_code=500,
    )

    assert error_response.error_code == 500
    assert error_response.error_type == ErrorType.SERVER_ERROR


def test_error_response_explicitly_sets_unknown_error():
    """Error responses use UNKNOWN_ERROR when explicitly specified."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    error_response = transport._error_response(
        request_id="test-explicit",
        message="unknown error",
        error_type=ErrorType.UNKNOWN_ERROR,
        error_code=None,
    )

    assert error_response.error_type == ErrorType.UNKNOWN_ERROR
    assert error_response.error is not None


# ===== Phase 5: Retry Behavior Tests =====

def test_safe_method_retries_on_timeout():
    """Safe read methods retry once on timeout."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    call_count = 0

    def mock_send_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise socket.timeout("timeout")
        # Success on second attempt
        return ResponseMessage(
            request_id="test-retry-timeout",
            result={"value": 123},
            error=None,
            error_type=None,
            error_code=None,
            latency_ms=50,
        )

    # Patch _send_once to simulate timeout then success
    with patch.object(transport, '_send_once', side_effect=mock_send_once):
        import asyncio
        request = RequestMessage(
            request_id="test-retry-timeout",
            command="execute_jsonrpc",
            args={"method": "Application.GetProperties", "params": {}},
        )
        response = asyncio.get_event_loop().run_until_complete(
            transport.send_request(request)
        )

        assert call_count == 2, "Should retry once on timeout"
        assert response.result == {"value": 123}
        assert response.error is None


def test_mutating_method_does_not_retry_on_timeout():
    """Mutating methods do NOT retry on timeout."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    call_count = 0

    def mock_send_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Return an error response directly (simulating what _send_once would do)
        return transport._error_response(
            request_id="test-no-retry-mutate",
            message="request timeout",
            error_type=ErrorType.TIMEOUT,
        )

    with patch.object(transport, '_send_once', side_effect=mock_send_once):
        import asyncio
        request = RequestMessage(
            request_id="test-no-retry-mutate",
            command="execute_jsonrpc",
            args={"method": "Addons.SetAddonEnabled", "params": {"addonid": "test", "enabled": True}},
        )
        response = asyncio.get_event_loop().run_until_complete(
            transport.send_request(request)
        )

        assert call_count == 1, "Mutating method should not retry"
        assert response.error_type == ErrorType.TIMEOUT
        assert response.error == "request timeout"


def test_auth_error_does_not_retry():
    """Auth errors are returned immediately, no retry."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    call_count = 0

    def mock_send_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # HTTPError is caught in _send_once, so _retry_wrapper receives a ResponseMessage
        # For auth errors, _send_once returns an error response, which should not retry
        return transport._error_response(
            request_id="test-no-retry-auth",
            message="http error 401: Unauthorized",
            error_type=ErrorType.AUTH_ERROR,
            error_code=401,
        )

    with patch.object(transport, '_send_once', side_effect=mock_send_once):
        import asyncio
        request = RequestMessage(
            request_id="test-no-retry-auth",
            command="execute_jsonrpc",
            args={"method": "Application.GetProperties", "params": {}},
        )
        response = asyncio.get_event_loop().run_until_complete(
            transport.send_request(request)
        )

        assert call_count == 1, "Auth error should not retry"
        assert response.error_type == ErrorType.AUTH_ERROR


def test_network_error_retries_once():
    """Network errors retry once."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    call_count = 0

    def mock_send_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise URLError("connection refused")
        # Success on second attempt
        return ResponseMessage(
            request_id="test-retry-network",
            result={"value": 456},
            error=None,
            error_type=None,
            error_code=None,
            latency_ms=60,
        )

    with patch.object(transport, '_send_once', side_effect=mock_send_once):
        import asyncio
        request = RequestMessage(
            request_id="test-retry-network",
            command="execute_jsonrpc",
            args={"method": "Files.GetSources", "params": {}},
        )
        response = asyncio.get_event_loop().run_until_complete(
            transport.send_request(request)
        )

        assert call_count == 2, "Should retry once on network error"
        assert response.result == {"value": 456}
        assert response.error is None


def test_max_one_retry():
    """Max 1 retry - gives up after 2 attempts."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="user",
        password="pass",
        timeout=10,
    )

    call_count = 0

    def mock_send_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise socket.timeout("timeout")

    with patch.object(transport, '_send_once', side_effect=mock_send_once):
        import asyncio
        request = RequestMessage(
            request_id="test-max-retry",
            command="execute_jsonrpc",
            args={"method": "Application.GetProperties", "params": {}},
        )
        response = asyncio.get_event_loop().run_until_complete(
            transport.send_request(request)
        )

        assert call_count == 2, "Should retry exactly once (2 total attempts)"
        assert response.error_type == ErrorType.TIMEOUT


def test_is_safe_to_retry():
    """Safe method detection works correctly."""
    # Safe methods
    assert is_safe_to_retry("Application.GetProperties")
    assert is_safe_to_retry("Files.GetSources")
    assert is_safe_to_retry("JSONRPC.Version")
    assert is_safe_to_retry("Addons.GetAddonDetails")

    # Unsafe methods
    assert not is_safe_to_retry("Addons.SetAddonEnabled")
    assert not is_safe_to_retry("Addons.ExecuteAddon")
    assert not is_safe_to_retry("System.Reboot")
    assert not is_safe_to_retry("System.Shutdown")
