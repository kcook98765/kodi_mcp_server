import hashlib
import threading
import zipfile
from pathlib import Path

import pytest

from kodi_mcp_server.repo_ops import RepoPublisher


def _addon_zip(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "plugin.test/addon.xml",
            f'<addon id="plugin.test" name="Test" version="{version}" provider-name="test"/>',
        )
        archive.writestr("plugin.test/default.py", "pass\n")


def _existing_repo(repo_root: Path) -> tuple[Path, Path, Path]:
    dev = repo_root / "dev-repo"
    zip_path = dev / "zips" / "plugin.test" / "plugin.test-2.0.0.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"old-zip")
    xml = dev / "addons.xml"
    xml_bytes = b'<addons><addon id="plugin.old" version="1.0.0"/></addons>'
    xml.write_bytes(xml_bytes)
    md5 = dev / "addons.xml.md5"
    md5.write_text(
        f"{hashlib.md5(xml_bytes).hexdigest()}  addons.xml\n", encoding="utf-8"
    )
    return zip_path, xml, md5


def _publish(repo_root: Path, addon_zip: Path):
    return RepoPublisher(repo_root).publish_addon(
        addon_zip_path=str(addon_zip),
        addon_id="plugin.test",
        addon_name="Test",
        addon_version="2.0.0",
    )


def test_failure_while_preparing_temps_leaves_all_old_finals_and_cleans_temps(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.repo_ops as module

    repo_root = tmp_path / "repo"
    final_zip, xml, md5 = _existing_repo(repo_root)
    addon_zip = tmp_path / "build" / "plugin.test-2.0.0.zip"
    _addon_zip(addon_zip, "2.0.0")
    before = (final_zip.read_bytes(), xml.read_bytes(), md5.read_bytes())
    original = module._write_fsynced_temp
    calls = 0

    def fail_second(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected temp failure")
        return original(path, data)

    monkeypatch.setattr(module, "_write_fsynced_temp", fail_second)
    with pytest.raises(OSError, match="injected temp failure"):
        _publish(repo_root, addon_zip)

    assert (final_zip.read_bytes(), xml.read_bytes(), md5.read_bytes()) == before
    assert md5.read_text(encoding="utf-8") == (
        f"{hashlib.md5(xml.read_bytes()).hexdigest()}  addons.xml\n"
    )
    assert list((repo_root / "dev-repo").rglob(".publish-*")) == []


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_failure_during_any_final_replace_restores_complete_old_generation(
    tmp_path, monkeypatch, fail_at
):
    import kodi_mcp_server.repo_ops as module

    repo_root = tmp_path / "repo"
    final_zip, xml, md5 = _existing_repo(repo_root)
    addon_zip = tmp_path / "build" / "plugin.test-2.0.0.zip"
    _addon_zip(addon_zip, "2.0.0")
    before = (final_zip.read_bytes(), xml.read_bytes(), md5.read_bytes())
    original_replace = module.os.replace
    calls = 0

    def fail_selected_replace(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError(f"injected replace failure {fail_at}")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_selected_replace)
    with pytest.raises(OSError, match=f"injected replace failure {fail_at}"):
        _publish(repo_root, addon_zip)

    assert (final_zip.read_bytes(), xml.read_bytes(), md5.read_bytes()) == before
    assert md5.read_text(encoding="utf-8") == (
        f"{hashlib.md5(xml.read_bytes()).hexdigest()}  addons.xml\n"
    )
    assert list((repo_root / "dev-repo").rglob(".publish-*")) == []


def test_success_atomically_publishes_complete_xml_and_matching_md5(tmp_path):
    repo_root = tmp_path / "repo"
    _existing_repo(repo_root)
    addon_zip = tmp_path / "build" / "plugin.test-2.0.0.zip"
    _addon_zip(addon_zip, "2.0.0")

    result = _publish(repo_root, addon_zip)

    xml = repo_root / "dev-repo" / "addons.xml"
    md5 = repo_root / "dev-repo" / "addons.xml.md5"
    xml_bytes = xml.read_bytes()
    assert result["status"] == "success"
    assert b'plugin.test' in xml_bytes
    assert md5.read_text(encoding="utf-8") == (
        f"{hashlib.md5(xml_bytes).hexdigest()}  addons.xml\n"
    )
    assert list((repo_root / "dev-repo").rglob(".publish-*")) == []


def test_third_replace_failure_removes_new_finals_that_did_not_previously_exist(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.repo_ops as module

    repo_root = tmp_path / "repo"
    addon_zip = tmp_path / "build" / "plugin.test-2.0.0.zip"
    _addon_zip(addon_zip, "2.0.0")
    original_replace = module.os.replace
    calls = 0

    def fail_third(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("third replace failed")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_third)
    with pytest.raises(OSError, match="third replace failed"):
        _publish(repo_root, addon_zip)

    assert not (repo_root / "dev-repo" / "addons.xml").exists()
    assert not (repo_root / "dev-repo" / "addons.xml.md5").exists()
    assert not (
        repo_root
        / "dev-repo"
        / "zips"
        / "plugin.test"
        / "plugin.test-2.0.0.zip"
    ).exists()


def test_cooperating_snapshot_cannot_observe_prepared_but_unpublished_metadata(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.repo_ops as module
    from kodi_mcp_server.orchestration_snapshot import create_repository_snapshot

    repo_root = tmp_path / "repo"
    _existing_repo(repo_root)
    addon_zip = tmp_path / "build" / "plugin.test-2.0.0.zip"
    _addon_zip(addon_zip, "2.0.0")
    prepared = threading.Event()
    release = threading.Event()
    snapshot_done = threading.Event()
    original = module._write_fsynced_temp
    calls = 0
    snapshot_payload = {}

    def pause_after_first_temp(path, data):
        nonlocal calls
        result = original(path, data)
        calls += 1
        if calls == 1:
            prepared.set()
            release.wait(5)
        return result

    monkeypatch.setattr(module, "_write_fsynced_temp", pause_after_first_temp)
    publisher = threading.Thread(target=_publish, args=(repo_root, addon_zip))

    def freeze():
        with create_repository_snapshot(
            source_dir=repo_root / "dev-repo",
            snapshot_root=tmp_path / "snapshots",
        ) as snapshot:
            with snapshot.open_reader() as reader, zipfile.ZipFile(reader) as archive:
                snapshot_payload["xml"] = archive.read("addons.xml")
                snapshot_payload["md5"] = archive.read("addons.xml.md5")
        snapshot_done.set()

    publisher.start()
    assert prepared.wait(5)
    freezer = threading.Thread(target=freeze)
    freezer.start()
    assert snapshot_done.wait(0.2) is False
    release.set()
    publisher.join(5)
    freezer.join(5)

    xml = snapshot_payload["xml"]
    assert b"plugin.test" in xml
    assert snapshot_payload["md5"] == (
        f"{hashlib.md5(xml).hexdigest()}  addons.xml\n".encode("utf-8")
    )
