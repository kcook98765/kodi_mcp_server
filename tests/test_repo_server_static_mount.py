"""Regression tests for the /repo/content/ static mount in repo_server.mount_repo_static.

Pins the Kodi-facing metadata redirect decision (addons.xml.gz preferred, plain
addons.xml fallback, plain-text when no metadata), bounded file serving (exact
bytes, 404 on missing, no directory listing), and the startup-lifecycle fix:
repo content must become available when the repo tree appears AFTER the app was
created, without a server restart.
"""

import gzip
import importlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _gz_payload(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def _build_isolated_client(
    tmp_path: Path, monkeypatch, *, create_repo_tree: bool = True,
) -> tuple[TestClient, Path]:
    """Point the repo static mount at an isolated tmp repo and return (client, repo_root).

    Follows the repo's established isolation pattern: redirect AUTHORITATIVE_REPO_ROOT
    at the paths module, then reload config + repo_server so REPO_ROOT rebinds before
    mount_repo_static registers routes on a fresh app. With create_repo_tree=False the
    repo directory is left absent so callers can model a server that starts before the
    repo tree exists and materialize it later on the same app.
    """
    repo_root = tmp_path / "repo"
    if create_repo_tree:
        (repo_root / "dev-repo").mkdir(parents=True, exist_ok=True)

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
    # Don't follow redirects so the /repo/content/ decision is asserted directly.
    return TestClient(app, follow_redirects=False), repo_root


def test_repo_content_root_prefers_gz_and_serves_files(tmp_path: Path, monkeypatch):
    """With both gz and plain metadata present, /repo/content/ redirects to the gz,
    files serve with exact bytes, and missing files 404."""
    gz = _gz_payload("<addons/>")
    client, repo_root = _build_isolated_client(tmp_path, monkeypatch)
    dev_repo = repo_root / "dev-repo"
    (dev_repo / "addons.xml.gz").write_bytes(gz)
    (dev_repo / "addons.xml").write_bytes(b"<addons/>\n")
    (dev_repo / "zips").mkdir()

    # gz metadata present -> redirect to the gz, and 307 is the SDK default.
    resp = client.get("/repo/content/")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/repo/content/addons.xml.gz"

    # The redirected-to gz is actually served with exact bytes.
    assert client.get("/repo/content/addons.xml.gz").content == gz
    # A real plain file is served with exact bytes.
    assert client.get("/repo/content/addons.xml").content == b"<addons/>\n"
    # A missing file under the content mount is 404, not a crash.
    assert client.get("/repo/content/does-not-exist.xml").status_code == 404
    # The mount does not expose directory listings.
    assert client.get("/repo/content/zips").status_code == 404


def test_repo_content_root_falls_back_to_plain_xml(tmp_path: Path, monkeypatch):
    """With only plain addons.xml present, /repo/content/ redirects to it."""
    client, repo_root = _build_isolated_client(tmp_path, monkeypatch)
    (repo_root / "dev-repo" / "addons.xml").write_bytes(b"<addons/>\n")

    resp = client.get("/repo/content/")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/repo/content/addons.xml"


def test_repo_content_root_plain_text_when_no_metadata(tmp_path: Path, monkeypatch):
    """With no metadata files, /repo/content/ serves a plain-text placeholder (200)."""
    client, _ = _build_isolated_client(tmp_path, monkeypatch)
    resp = client.get("/repo/content/")
    assert resp.status_code == 200
    assert resp.text.strip() == "Kodi MCP Repository"


def test_repo_content_becomes_available_when_repo_tree_appears_after_app_creation(
    tmp_path: Path, monkeypatch
):
    """The confirmed lifecycle: app created before repo/dev-repo exists, tree staged
    afterwards, and the SAME app instance serves the content without a restart.

    Under the old boot-time design the /repo/content/* routes were registered only
    when the tree already existed at app creation, so they stayed missing (generic
    FastAPI 404) until the server restarted.
    """
    # App/server created while no repo tree exists at all.
    client, repo_root = _build_isolated_client(
        tmp_path, monkeypatch, create_repo_tree=False
    )
    assert not repo_root.exists()
    assert not (repo_root / "dev-repo").exists()

    # Before the tree exists: no metadata redirect and no files to serve.
    assert client.get("/repo/content/").text.strip() == "Kodi MCP Repository"
    assert client.get("/repo/content/addons.xml").status_code == 404

    # repo/dev-repo and addons.xml appear AFTER app creation (staged later at runtime).
    dev_repo = repo_root / "dev-repo"
    dev_repo.mkdir(parents=True, exist_ok=True)
    (dev_repo / "addons.xml").write_bytes(b"<addons>\n</addons>\n")

    # Same app instance, no restart: the content is now available.
    resp = client.get("/repo/content/")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/repo/content/addons.xml"
    assert client.get("/repo/content/addons.xml").content == b"<addons>\n</addons>\n"


def test_repo_content_serves_legitimate_nested_file_with_exact_bytes(
    tmp_path: Path, monkeypatch
):
    """A file in a subdirectory of the served root serves with exact bytes
    (control: the containment check must not over-restrict the happy path)."""
    client, repo_root = _build_isolated_client(tmp_path, monkeypatch)
    payload = b"PK\x03\x04" + b"nested-payload"
    (repo_root / "dev-repo" / "zips").mkdir(parents=True)
    (repo_root / "dev-repo" / "zips" / "addon-1.0.zip").write_bytes(payload)

    resp = client.get("/repo/content/zips/addon-1.0.zip")
    assert resp.status_code == 200
    assert resp.content == payload


def test_repo_content_encoded_traversal_escape_rejected(tmp_path: Path, monkeypatch):
    """A percent-encoded ``..`` that would resolve outside the served root is
    rejected with 404 and must not leak the decoy's bytes.

    The encoded form (``%2E%2E``) reaches the route decoded; a raw ``..`` is
    normalized away by the client before it arrives. The decoy lives just
    outside the served root (a sibling of ``dev-repo/`` under the repo root),
    so a pre-fix build would have served its contents.
    """
    client, repo_root = _build_isolated_client(tmp_path, monkeypatch)
    (repo_root / "dev-repo" / "addons.xml").write_bytes(b"<addons/>\n")
    decoy = repo_root / "decoy.txt"
    decoy.write_text("SHOULD_NEVER_BE_SERVED")

    resp = client.get("/repo/content/%2E%2E/decoy.txt")
    assert resp.status_code == 404
    # The decoy must not leak through the content surface.
    assert b"SHOULD_NEVER_BE_SERVED" not in resp.content


def test_repo_content_symlink_pointing_outside_rejected(tmp_path: Path, monkeypatch):
    """A symlink inside the served root whose target lives outside it cannot be
    used to read the target's bytes."""
    client, repo_root = _build_isolated_client(tmp_path, monkeypatch)
    (repo_root / "dev-repo" / "addons.xml").write_bytes(b"<addons/>\n")
    decoy = repo_root / "secret.txt"
    decoy.write_text("SYMLINK_ESCAPE_SECRET")
    (repo_root / "dev-repo" / "evil-link.txt").symlink_to(decoy)

    resp = client.get("/repo/content/evil-link.txt")
    assert resp.status_code == 404
    assert b"SYMLINK_ESCAPE_SECRET" not in resp.content
