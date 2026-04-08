"""Local server configuration for Kodi test transport.

Authoritative repo ownership:
- runtime serving/publishing uses project-root `repo/`
- legacy `server/repo*` paths are non-authoritative and must not be used

Canonical startup behavior:
- when running from `mcp_repo_server/`, load environment values from
  `mcp_repo_server/.env` if present so documented canonical commands work
  without extra shell export steps.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from .paths import AUTHORITATIVE_REPO_ROOT, PROJECT_ROOT, assert_not_legacy_repo_path


def _load_dotenv_if_present() -> None:
    """Load simple KEY=VALUE pairs from project root `.env` file."""
    env_path = PROJECT_ROOT / "project" / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv_if_present()


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""

    pass


def validate_config() -> None:
    """Validate that required configuration is present.

    Raises:
        ConfigError: If required configuration is missing.
    """
    missing = []

    if not KODI_JSONRPC_URL:
        missing.append("KODI_JSONRPC_URL")
    if not KODI_BRIDGE_BASE_URL:
        missing.append("KODI_BRIDGE_BASE_URL")

    if missing:
        raise ConfigError(
            f"Missing required configuration: {', '.join(missing)}\n"
            "Set these in .env file or as environment variables.\n"
            "See .env.example for required values."
        )


DEFAULT_KODI_JSONRPC_URL = "http://localhost:8080/jsonrpc"
DEFAULT_KODI_JSONRPC_USERNAME = ""
DEFAULT_KODI_JSONRPC_PASSWORD = ""
DEFAULT_KODI_TIMEOUT = 10
DEFAULT_KODI_TCP_HOST = ""
DEFAULT_KODI_TCP_PORT = 9090
DEFAULT_KODI_WEBSOCKET_URL = ""
DEFAULT_KODI_BRIDGE_BASE_URL = "http://localhost:8765"
DEFAULT_KODI_BRIDGE_TOKEN = ""
DEFAULT_REPO_SERVER_HOST = "0.0.0.0"
DEFAULT_REPO_SERVER_PORT = 8001
DEFAULT_REPO_BASE_URL = "http://claw.home.arpa:8000"

KODI_JSONRPC_URL = os.getenv("KODI_JSONRPC_URL", DEFAULT_KODI_JSONRPC_URL)
KODI_JSONRPC_USERNAME = os.getenv(
    "KODI_JSONRPC_USERNAME", DEFAULT_KODI_JSONRPC_USERNAME
)
KODI_JSONRPC_PASSWORD = os.getenv(
    "KODI_JSONRPC_PASSWORD", DEFAULT_KODI_JSONRPC_PASSWORD
)
KODI_TIMEOUT = int(os.getenv("KODI_TIMEOUT", str(DEFAULT_KODI_TIMEOUT)))

_default_tcp_host = ""
if KODI_JSONRPC_URL:
    parsed = urlparse(KODI_JSONRPC_URL)
    _default_tcp_host = parsed.hostname or ""

KODI_TCP_HOST = os.getenv("KODI_TCP_HOST", _default_tcp_host or DEFAULT_KODI_TCP_HOST)
KODI_TCP_PORT = int(os.getenv("KODI_TCP_PORT", str(DEFAULT_KODI_TCP_PORT)))
KODI_WEBSOCKET_URL = os.getenv("KODI_WEBSOCKET_URL", DEFAULT_KODI_WEBSOCKET_URL)
KODI_BRIDGE_BASE_URL = os.getenv("KODI_BRIDGE_BASE_URL", DEFAULT_KODI_BRIDGE_BASE_URL)
KODI_BRIDGE_TOKEN = os.getenv("KODI_BRIDGE_TOKEN", DEFAULT_KODI_BRIDGE_TOKEN)
REPO_SERVER_HOST = os.getenv("REPO_SERVER_HOST", DEFAULT_REPO_SERVER_HOST)
REPO_SERVER_PORT = int(os.getenv("REPO_SERVER_PORT", str(DEFAULT_REPO_SERVER_PORT)))
REPO_BASE_URL = os.getenv("REPO_BASE_URL", DEFAULT_REPO_BASE_URL)

# Authoritative Kodi repo directory used by all runtime serving/publishing code.
REPO_ROOT = assert_not_legacy_repo_path(AUTHORITATIVE_REPO_ROOT)
