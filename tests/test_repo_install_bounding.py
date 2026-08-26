"""Regression tests for bounded serving on the /repo/install/* surface.

Pins that ``/repo/install/{filename}`` serves only files that stay inside the
published ``repo-addon/`` directory. Path-escape attempts (``../`` segments,
including the percent-encoded ``%2E%2E`` form that survives URL normalization)
must be rejected with 404, not resolved to files that live outside the
published package location.

The install side is the highest-value uncovered behavior here:
``/repo/install/{filename}`` is the only install endpoint that takes a
user-controlled path, and its docstring promises to "only serve files from
the published repo addon location."
"""

import importlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_isolated_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    """Point the repo install endpoints at an isolated tmp tree.

    Mirrors the isolation pattern in test_repo_server_static_mount: redirect
    AUTHORITATIVE_REPO_ROOT at the paths module, then reload config + repo_server
    so REPO_ROOT rebinds before the routes are registered on a fresh app.

    The install endpoints source from ``REPO_ROOT.parent / "repo-addon"``, so we
    place the published zips under ``tmp_path/repo-addon`` (a sibling of
    ``tmp_path/repo``).
    """
    repo_root = tmp_path / "repo"
    (repo_root / "dev-repo").mkdir(parents=True)
    repo_addon = tmp_path / "repo-addon"
    repo_addon.mkdir()

    monkeypatch.setenv("REPO_BASE_URL", "http://testserver")

    import kodi_mcp_server.paths as paths

    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)

    import kodi_mcp_server.config as config
    import kodi_mcp_server.repo_server as repo_server

    importlib.reload(config)
    importlib.reload(repo_server)

    from kodi_mcp_server.repo_app import configure_repo_app

    app = FastAPI()
    configure_repo_app(app)
    return TestClient(app, follow_redirects=False), repo_addon


def test_install_named_versioned_zip_served(tmp_path: Path, monkeypatch):
    """A legitimate, named versioned package inside repo-addon/ is served with
    exact bytes (control: the fix must not over-restrict the happy path)."""
    client, repo_addon = _build_isolated_client(tmp_path, monkeypatch)
    payload = b"PK\x03\x04" + b"versioned-payload-1.0.0"
    (repo_addon / "repository.kodi-mcp-1.0.0.zip").write_bytes(payload)

    resp = client.get("/repo/install/repository.kodi-mcp-1.0.0.zip")
    assert resp.status_code == 200
    assert resp.content == payload


def test_install_traversal_escape_rejected(tmp_path: Path, monkeypatch):
    """A request that would resolve outside repo-addon/ is rejected with 404.

    The percent-encoded form (``%2E%2E``) is used because a raw ``..`` is
    normalized away by the HTTP client before it reaches the server; the encoded
    form reaches the route decoded and is the real escape vector. The target is a
    file that legitimately exists but lives OUTSIDE the published package
    location (``repo/dev-repo/addons.xml``), so a pre-fix build would have served
    its contents.
    """
    client, repo_addon = _build_isolated_client(tmp_path, monkeypatch)
    # A published package exists (so the directory is not "empty"), and a
    # decoy file exists just outside repo-addon/, reachable via ../.
    (repo_addon / "repository.kodi-mcp-1.0.0.zip").write_bytes(b"PK\x03\x04" + b"x" * 16)
    secret = repo_addon.parent / "repo" / "dev-repo" / "addons.xml"
    secret.write_text("<addons><secret>SHOULD_NEVER_BE_SERVED</secret></addons>")

    resp = client.get("/repo/install/%2E%2E/repo/dev-repo/addons.xml")
    assert resp.status_code == 404
    # The secret must not leak through the install surface.
    assert b"SHOULD_NEVER_BE_SERVED" not in resp.content
