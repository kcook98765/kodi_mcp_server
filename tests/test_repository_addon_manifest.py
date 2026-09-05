from __future__ import annotations

import asyncio
import hashlib
import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def test_manifest_is_the_canonical_1_0_4_identity():
    from kodi_mcp_server.repository_addon_manifest import load_repository_addon_manifest

    manifest = load_repository_addon_manifest()
    assert manifest.addon_id == "repository.kodi-mcp"
    assert manifest.version == "1.0.4"
    assert manifest.repository_extension == "xbmc.addon.repository"
    assert "url" not in manifest.__dict__


def test_generator_uses_manifest_and_dynamic_url_deterministically(tmp_path: Path):
    from kodi_mcp_server.repo_generator import build_repo_addon
    from kodi_mcp_server.repository_addon_manifest import load_repository_addon_manifest

    manifest = load_repository_addon_manifest()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    base_url = "http://kodi-server.lan:8010"

    for output in (first, second):
        result = build_repo_addon(
            repo_base_url=base_url,
            output_zip=output,
            repo_root=repo_root,
        )
        assert result["status"] == "ok"
        assert result["repo_version"] == manifest.version
        assert result["addon_id"] == manifest.addon_id

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    with zipfile.ZipFile(first) as archive:
        root = ElementTree.fromstring(archive.read(f"{manifest.addon_id}/addon.xml"))
    assert root.get("id") == manifest.addon_id
    assert root.get("version") == manifest.version
    rendered = ElementTree.tostring(root, encoding="unicode")
    assert base_url in rendered
    assert "172.27.0.1" not in rendered


def test_canonical_artifact_selection_ignores_misleading_mtimes(tmp_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import kodi_mcp_server.repo_server as repo_server
    from kodi_mcp_server.repository_addon_manifest import load_repository_addon_manifest

    manifest = load_repository_addon_manifest()
    repo_root = tmp_path / "repo"
    repo_addon = tmp_path / "repo-addon"
    repo_root.mkdir()
    repo_addon.mkdir()
    canonical = repo_addon / manifest.artifact_filename
    older = repo_addon / "repository.kodi-mcp-1.0.3.zip"
    newer = repo_addon / "repository.kodi-mcp-9.9.9.zip"
    for path in (canonical, older, newer):
        path.write_bytes(path.name.encode("ascii"))
    os.utime(canonical, (1, 1))
    os.utime(older, (2, 2))
    os.utime(newer, (3, 3))

    assert repo_server.canonical_repository_artifact(repo_addon) == canonical
    original_root = repo_server.REPO_ROOT
    repo_server.REPO_ROOT = repo_root
    try:
        app = FastAPI()
        app.include_router(repo_server.router)
        response = TestClient(app).get("/repo/install/latest.zip")
    finally:
        repo_server.REPO_ROOT = original_root
    assert response.status_code == 200
    assert response.content == canonical.read_bytes()


def test_automatic_staging_uses_manifest_version(tmp_path: Path, monkeypatch):
    import kodi_mcp_server.milestone_a_bridge as milestone
    import kodi_mcp_server.paths as paths
    import kodi_mcp_server.repo_generator as repo_generator
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.repository_addon_manifest import load_repository_addon_manifest

    manifest = load_repository_addon_manifest()
    stop_event = asyncio.Event()
    artifact = tmp_path / manifest.artifact_filename
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr(
            f"{manifest.addon_id}/addon.xml",
            '<addon id="repository.kodi-mcp" version="1.0.4">'
            '<extension point="xbmc.addon.repository" /></addon>',
        )
    staged = {}

    async def register(payload):
        stop_event.set()
        return milestone.EnvelopeResult(True, True, {"result": {"ok": True}}), object()

    async def state():
        return (
            milestone.EnvelopeResult(
                True,
                True,
                {
                    "result": {
                        "ok": True,
                        "registration": {"applied_ttl_seconds": 60},
                        "derived": {
                            "registration_present": True,
                            "registration_stale": False,
                            "dev_setup_available": False,
                            "repo_zip_file_exists": False,
                        },
                    }
                },
            ),
            object(),
        )

    async def stage(*, zip_path, repo_version=None, verify=True):
        staged.update(zip_path=zip_path, repo_version=repo_version, verify=verify)
        return {"state": {"dev_setup_available": True}}

    monkeypatch.setattr(milestone, "register_with_addon", register)
    monkeypatch.setattr(milestone, "read_addon_state", state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", stage)
    monkeypatch.setattr(repo_generator, "build_repo_addon", lambda: {
        "status": "ok",
        "output_zip": str(artifact),
        "repo_version": manifest.version,
    })
    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", tmp_path / "repo")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_addon_registration_loop(stop_event=stop_event))
    finally:
        loop.close()

    assert staged == {
        "zip_path": str(artifact),
        "repo_version": manifest.version,
        "verify": True,
    }
