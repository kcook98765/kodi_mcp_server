"""Tests for endpoint response structure."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

# Compose the full app (repo + tool routes) via the canonical composition module.
from kodi_mcp_server.main import app


def test_health_endpoint_exists():
    """GET /health returns valid JSON structure."""
    client = TestClient(app)
    response = client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_status_endpoint_exists():
    """GET /status returns valid JSON structure."""
    client = TestClient(app)
    response = client.get("/status")
    
    # Even if connection fails, structure should be correct
    assert response.status_code == 200
    data = response.json()
    
    # Verify expected keys exist
    assert "server" in data
    assert "config" in data
    assert "jsonrpc" in data
    assert "bridge" in data


def test_status_endpoint_has_correct_structure():
    """GET /status returns expected nested structure."""
    client = TestClient(app)
    response = client.get("/status")
    
    assert response.status_code == 200
    data = response.json()
    
    # Server section
    assert "status" in data["server"]
    
    # Config section
    assert "loaded" in data["config"]
    
    # JSON-RPC section
    assert "status" in data["jsonrpc"]
    assert "url" in data["jsonrpc"]
    
    # Bridge section
    assert "status" in data["bridge"]
    assert "url" in data["bridge"]
