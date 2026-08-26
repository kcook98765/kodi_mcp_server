"""Tests for config validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from kodi_mcp_server import config
from kodi_mcp_server import paths
from kodi_mcp_server.config import validate_config


def test_validate_config_success_with_all_values(monkeypatch):
    """validate_config() succeeds when all required values present."""
    monkeypatch.setattr(config, "KODI_JSONRPC_URL", "http://test:8080/jsonrpc")
    monkeypatch.setattr(config, "KODI_BRIDGE_BASE_URL", "http://test:8765")
    validate_config()  # Should not raise


def test_validate_config_raises_when_values_missing(monkeypatch):
    """validate_config() raises ConfigError when required values are missing."""
    monkeypatch.setattr(config, "KODI_JSONRPC_URL", "")
    monkeypatch.setattr(config, "KODI_BRIDGE_BASE_URL", "")
    with pytest.raises(config.ConfigError, match="Missing required configuration"):
        validate_config()


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


def test_project_root_resolves_to_repository_root():
    """``paths.PROJECT_ROOT`` must be the repository root, not its parent.

    The package is an editable install laid out under ``src/``::

        <repo>/pyproject.toml
        <repo>/src/kodi_mcp_server/paths.py

    so the repository root (the directory holding ``pyproject.toml``, the
    documented ``.env`` location, and ``src/``) is two levels above
    ``paths.py``. A one-level-too-high resolution (``parents[3]``) lands on
    ``<repo>``'s *parent* and breaks every derived root, so canonical
    startup no longer loads ``<repo>/.env``.

    This asserts the real, un-monkeypatched value against the filesystem
    marker ``pyproject.toml`` — independent of any live ``.env`` value.
    """
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "pyproject.toml").is_file(), "sanity: repo root marker present"
    assert (repo_root / "src" / "kodi_mcp_server").is_dir(), "sanity: src layout present"
    assert paths.PROJECT_ROOT == repo_root


def test_dotenv_lookup_target_is_repository_root_env():
    """The primary ``.env`` lookup must target ``<repo>/.env``.

    Regression guard for the root-resolution bug: with ``PROJECT_ROOT``
    resolving one level too high, the loader looked for the ``.env`` in the
    repository's *parent* directory and silently fell back to defaults.
    Assert the lookup path itself (structural, no live ``.env`` value read)
    so a regression to ``parents[3]`` fails even when no ``.env`` is present.
    """
    repo_root = Path(__file__).resolve().parents[1]
    # The real .env lives at the repo root in this checkout; the structural
    # assertion holds regardless of its contents.
    assert config.PROJECT_ROOT == repo_root
    assert config.PROJECT_ROOT / ".env" == repo_root / ".env"
    # And that marker actually exists in the source tree (repo-root .env).
    assert (repo_root / ".env").is_file()
