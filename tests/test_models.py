"""Tests for ResponseMessage serialization and structure."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kodi_mcp_server.models.messages import (
    ErrorType,
    ResponseMessage,
    RequestMessage,
)


def test_response_message_serialization_success():
    """ResponseMessage.to_dict() serializes success response correctly."""
    rm = ResponseMessage(
        request_id="test-123",
        result={"status": "ok", "data": [1, 2, 3]},
        error=None,
        error_type=None,
        error_code=None,
        latency_ms=42,
    )

    data = rm.to_dict()

    assert data["request_id"] == "test-123"
    assert data["result"] == {"status": "ok", "data": [1, 2, 3]}
    assert data["error"] is None
    assert data["error_type"] is None
    assert data["error_code"] is None
    assert data["latency_ms"] == 42


def test_response_message_serialization_error():
    """ResponseMessage.to_dict() serializes error response correctly."""
    rm = ResponseMessage(
        request_id="test-456",
        result=None,
        error="connection failed",
        error_type=ErrorType.NETWORK_ERROR,
        error_code=None,
        latency_ms=150,
    )

    data = rm.to_dict()

    assert data["request_id"] == "test-456"
    assert data["result"] is None
    assert data["error"] == "connection failed"
    assert data["error_type"] == "network_error"
    assert data["error_code"] is None
    assert data["latency_ms"] == 150


def test_response_message_from_dict_success():
    """ResponseMessage.from_dict() parses success response correctly."""
    data = {
        "request_id": "test-789",
        "result": {"status": "ok"},
        "error": None,
        "error_type": None,
        "error_code": None,
        "latency_ms": 10,
    }

    rm = ResponseMessage.from_dict(data)

    assert rm.request_id == "test-789"
    assert rm.result == {"status": "ok"}
    assert rm.error is None
    assert rm.error_type is None
    assert rm.error_code is None
    assert rm.latency_ms == 10


def test_response_message_from_dict_error():
    """ResponseMessage.from_dict() parses error response correctly."""
    data = {
        "request_id": "test-abc",
        "result": None,
        "error": "auth failed",
        "error_type": "auth_error",
        "error_code": 401,
        "latency_ms": 5,
    }

    rm = ResponseMessage.from_dict(data)

    assert rm.request_id == "test-abc"
    assert rm.result is None
    assert rm.error == "auth failed"
    assert rm.error_type == ErrorType.AUTH_ERROR
    assert rm.error_code == 401
    assert rm.latency_ms == 5


def test_response_message_from_dict_unknown_error_type():
    """ResponseMessage.from_dict() handles unknown error types."""
    data = {
        "request_id": "test-def",
        "result": None,
        "error": "unknown",
        "error_type": "some_unknown_type",
        "error_code": None,
        "latency_ms": None,
    }

    rm = ResponseMessage.from_dict(data)

    assert rm.error_type == ErrorType.UNKNOWN_ERROR


def test_request_message_serialization():
    """RequestMessage.to_dict() serializes correctly."""
    rm = RequestMessage(
        request_id="req-123",
        command="execute_jsonrpc",
        args={"method": "Test.Method", "params": {"key": "value"}},
    )

    data = rm.to_dict()

    assert data["request_id"] == "req-123"
    assert data["command"] == "execute_jsonrpc"
    assert data["args"] == {"method": "Test.Method", "params": {"key": "value"}}
