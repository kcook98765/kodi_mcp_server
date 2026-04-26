"""Tests for config validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kodi_mcp_server import config
from kodi_mcp_server.config import validate_config


def test_validate_config_success_with_all_values():
    """validate_config() succeeds when all required values present."""
    # This test verifies that validation passes with valid config
    # Note: The .env file in project/ is loaded at module import time
    # This test assumes the workspace has valid KODI_JSONRPC_URL and KODI_BRIDGE_BASE_URL
    validate_config()  # Should not raise


def test_load_dotenv_supports_repo_root_env(tmp_path, monkeypatch):
    """Root `.env` values are loaded for documented repo-root startup."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("KODI_JSONRPC_URL", raising=False)
    monkeypatch.delenv("KODI_BRIDGE_BASE_URL", raising=False)

    (tmp_path / ".env").write_text(
        "KODI_JSONRPC_URL=http://root-env:8080/jsonrpc\n"
        "KODI_BRIDGE_BASE_URL='http://root-env:8765'\n",
        encoding="utf-8",
    )

    config._load_dotenv_if_present()

    assert config.os.environ["KODI_JSONRPC_URL"] == "http://root-env:8080/jsonrpc"
    assert config.os.environ["KODI_BRIDGE_BASE_URL"] == "http://root-env:8765"


def test_load_dotenv_preserves_existing_environment(tmp_path, monkeypatch):
    """Existing process env values take precedence over local `.env` files."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("KODI_JSONRPC_URL", "http://existing:8080/jsonrpc")

    (tmp_path / ".env").write_text(
        "KODI_JSONRPC_URL=http://root-env:8080/jsonrpc\n",
        encoding="utf-8",
    )

    config._load_dotenv_if_present()

    assert config.os.environ["KODI_JSONRPC_URL"] == "http://existing:8080/jsonrpc"
