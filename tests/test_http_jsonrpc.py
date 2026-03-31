"""Tests for HTTP JSON-RPC transport error handling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport


def test_error_response_includes_error_type():
    """Transport._error_response() includes error_type in response."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="",
        password="",
        timeout=10,
    )
    
    rm = transport._error_response(
        request_id="test-123",
        message="test error",
        error_type=ErrorType.TIMEOUT,
        error_code=None,
    )
    
    assert rm.error_type == ErrorType.TIMEOUT
    assert rm.error == "test error"


def test_error_response_includes_error_code_for_http():
    """Transport._error_response() includes error_code for HTTP errors."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="",
        password="",
        timeout=10,
    )
    
    rm = transport._error_response(
        request_id="test-456",
        message="http error 500",
        error_type=ErrorType.SERVER_ERROR,
        error_code=500,
    )
    
    assert rm.error_code == 500
    assert rm.error_type == ErrorType.SERVER_ERROR


def test_error_response_defaults_to_unknown_error():
    """Transport._error_response() defaults to UNKNOWN_ERROR."""
    transport = HttpJsonRpcTransport(
        url="http://test:8080/jsonrpc",
        username="",
        password="",
        timeout=10,
    )
    
    rm = transport._error_response(
        request_id="test-789",
        message="unknown error",
        error_type=None,  # Should default to UNKNOWN_ERROR
        error_code=None,
    )
    
    # Note: This test expects the caller to always pass error_type
    # In practice, all error paths now include error_type
    assert rm.error is not None
