"""Tests for HTTP error mapping in HttpJsonRpcTransport."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from kodi_mcp_server.models.messages import ErrorType
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport
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
