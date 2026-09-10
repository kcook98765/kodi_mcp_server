import contextlib
import asyncio
import json
import multiprocessing
import zipfile
from pathlib import Path

from kodi_mcp_server.artifact_store import ArtifactStore
from kodi_mcp_server.artifacts import AddonArtifact
from kodi_mcp_server.repo_ops import RepoPublisher


def _recording_lock(calls):
    @contextlib.contextmanager
    def acquire(*, timeout=30.0, lock_root=None):
        calls.append(Path(lock_root) if lock_root is not None else None)
        yield

    return acquire


def _addon_zip(path: Path, addon_id: str = "plugin.test", version: str = "1.0.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{addon_id}/addon.xml",
            f'<addon id="{addon_id}" name="Test" version="{version}" provider-name="test"/>',
        )
        archive.writestr(f"{addon_id}/default.py", "pass\n")


def _hold_repo_lock(repo_root: str, ready, release) -> None:
    from kodi_mcp_server.orchestration_locking import acquire_global_workflow_lock

    with acquire_global_workflow_lock(timeout=5):
        ready.set()
        release.wait(5)


def _generate_gzip(repo_root: str, output: str, started, done) -> None:
    from kodi_mcp_server.repo_generator import generate_addons_xml_gz

    started.set()
    generate_addons_xml_gz(repo_root=Path(repo_root), output=Path(output))
    done.set()


def _hold_real_snapshot(source: str, snapshot_root: str, ready, release) -> None:
    from kodi_mcp_server.orchestration_snapshot import create_repository_snapshot

    def on_lock_acquired():
        ready.set()
        release.wait(5)

    with create_repository_snapshot(
        source_dir=Path(source),
        snapshot_root=Path(snapshot_root),
        _on_lock_acquired=on_lock_acquired,
    ):
        pass


def _publish_repo(repo_root: str, addon_zip: str, started, done) -> None:
    started.set()
    RepoPublisher(Path(repo_root)).publish_addon(
        addon_zip_path=addon_zip,
        addon_id="plugin.test",
        addon_name="Test",
        addon_version="1.0.0",
    )
    done.set()


def test_artifact_store_writes_use_global_lock_and_atomic_index(tmp_path, monkeypatch):
    import kodi_mcp_server.artifact_store as module

    calls = []
    monkeypatch.setattr(module, "acquire_global_workflow_lock", _recording_lock(calls))
    store = ArtifactStore(tmp_path / "artifacts")
    first = store.register_bytes(data=b"first", artifact_id="first")
    second = store.register_existing_file(
        file_path=Path(first.path), artifact_id="second"
    )

    assert len(calls) == 2
    assert all(path is None for path in calls)
    index = json.loads((tmp_path / "artifacts" / "index.json").read_text(encoding="utf-8"))
    assert sorted(index["artifacts"]) == ["first", "second"]


def test_artifact_store_registration_remains_reentrant_under_global_lock(tmp_path):
    from kodi_mcp_server.orchestration_locking import acquire_global_workflow_lock

    store = ArtifactStore(tmp_path / "artifacts")
    with acquire_global_workflow_lock(timeout=1):
        record = store.register_bytes(data=b"nested", artifact_id="nested")

    assert Path(record.path).read_bytes() == b"nested"
    assert store.get("nested") == record


def test_repo_publisher_uses_global_lock_for_zip_metadata_and_md5(tmp_path, monkeypatch):
    import kodi_mcp_server.repo_ops as module

    calls = []
    monkeypatch.setattr(module, "acquire_global_workflow_lock", _recording_lock(calls))
    addon_zip = tmp_path / "build" / "plugin.test-1.0.0.zip"
    _addon_zip(addon_zip)

    RepoPublisher(tmp_path / "repo").publish_addon(
        addon_zip_path=str(addon_zip),
        addon_id="plugin.test",
        addon_name="Test",
        addon_version="1.0.0",
    )

    assert calls == [None]
    assert (tmp_path / "repo" / "dev-repo" / "addons.xml").is_file()
    assert (tmp_path / "repo" / "dev-repo" / "addons.xml.md5").is_file()


def test_managed_registry_and_build_outputs_use_global_lock(tmp_path, monkeypatch):
    import kodi_mcp_server.managed_addons as module

    calls = []
    source = tmp_path / "source" / "plugin.test"
    source.mkdir(parents=True)
    (source / "addon.xml").write_text(
        '<addon id="plugin.test" name="Test" version="1.0.0"/>', encoding="utf-8"
    )
    (source / "default.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(module, "PROJECT_DIR", tmp_path / "project")
    monkeypatch.setattr(module, "REGISTRY_PATH", tmp_path / "project" / "managed_addons.json")
    monkeypatch.setattr(module, "LEGACY_ADDON_ARTIFACTS_ROOT", tmp_path / "addon")
    monkeypatch.setattr(module, "AUTHORITATIVE_REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(module, "acquire_global_workflow_lock", _recording_lock(calls))

    module.managed_addon_register(str(source))
    module.managed_addon_build("plugin.test", "use_addon_xml")
    dev_repo = tmp_path / "repo" / "dev-repo"
    dev_repo.mkdir(parents=True)
    (dev_repo / "addons.xml").write_text("<addons/>", encoding="utf-8")
    module.build_dev_repo_zip()

    assert len(calls) >= 3
    assert all(path is None for path in calls)


def test_repo_generator_outputs_use_global_lock(tmp_path, monkeypatch):
    import kodi_mcp_server.repo_generator as module

    calls = []
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "addons.xml").write_text("<addons/>", encoding="utf-8")
    monkeypatch.setattr(module, "acquire_global_workflow_lock", _recording_lock(calls))

    built = module.build_repo_addon(
        repo_version="1.0.4",
        repo_base_url="http://example.invalid",
        output_zip=tmp_path / "repo-addon" / "repository.zip",
        repo_root=repo_root,
    )
    module.generate_addons_xml_gz(repo_root=repo_root)

    assert built["status"] == "ok"
    assert calls == [None, None]


def test_real_dev_repo_gzip_writer_contends_on_snapshot_global_lock(tmp_path):
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    dev_repo = AUTHORITATIVE_REPO_ROOT / "dev-repo"
    assert (dev_repo / "addons.xml").is_file()
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    started = ctx.Event()
    done = ctx.Event()
    holder = ctx.Process(
        target=_hold_real_snapshot,
        args=(str(dev_repo), str(tmp_path / "snapshots"), ready, release),
    )
    writer = ctx.Process(
        target=_generate_gzip,
        args=(str(dev_repo), str(tmp_path / "addons.xml.gz"), started, done),
    )
    holder.start()
    assert ready.wait(5)
    writer.start()
    assert started.wait(5)
    assert done.wait(0.2) is False
    release.set()
    assert done.wait(5)
    holder.join(5)
    writer.join(5)

    assert holder.exitcode == writer.exitcode == 0
    assert (tmp_path / "addons.xml.gz").is_file()


def test_addon_artifact_build_uses_global_lock(tmp_path, monkeypatch):
    import kodi_mcp_server.artifacts as module

    calls = []
    monkeypatch.setattr(module, "acquire_global_workflow_lock", _recording_lock(calls))
    artifact = AddonArtifact(
        addon_id="plugin.test",
        addon_name="Test",
        addon_version="1.0.0",
        package_root=tmp_path / "packages",
        build_root=tmp_path / "addon",
        repo_root=tmp_path / "repo",
    )
    artifact.source_dir.mkdir(parents=True)
    (artifact.source_dir / "addon.xml").write_text(
        '<addon id="plugin.test" name="Test" version="1.0.0"/>', encoding="utf-8"
    )

    artifact.build_legacy_zip()

    assert calls == [None]


def test_repo_publisher_blocks_cross_process_while_global_lock_is_held(tmp_path):
    repo_root = tmp_path / "repo"
    addon_zip = tmp_path / "build" / "plugin.test-1.0.0.zip"
    _addon_zip(addon_zip)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    started = ctx.Event()
    done = ctx.Event()
    holder = ctx.Process(target=_hold_repo_lock, args=(str(repo_root), ready, release))
    writer = ctx.Process(
        target=_publish_repo,
        args=(str(repo_root), str(addon_zip), started, done),
    )
    holder.start()
    assert ready.wait(5)
    writer.start()
    assert started.wait(5)
    assert done.wait(0.2) is False
    release.set()
    assert done.wait(5)
    holder.join(5)
    writer.join(5)

    assert holder.exitcode == writer.exitcode == 0
    assert (repo_root / "dev-repo" / "addons.xml").is_file()


def test_dev_repo_preparation_is_locked_but_network_stage_is_not(tmp_path, monkeypatch):
    import kodi_mcp_server.dev_loop_artifacts as module
    import kodi_mcp_server.managed_addons as managed
    import kodi_mcp_server.milestone_a_bridge as bridge

    active = []
    calls = []

    @contextlib.contextmanager
    def recording_lock(*, timeout=30.0, lock_root=None):
        calls.append(Path(lock_root) if lock_root is not None else None)
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    repo_root = tmp_path / "repo"

    def initialize(*, repo_root):
        assert active
        dev_repo = repo_root / "dev-repo"
        dev_repo.mkdir(parents=True)
        (dev_repo / "addons.xml").write_text("<addons/>", encoding="utf-8")

    def build(repo_version=None):
        assert active
        output = tmp_path / "addon" / "dev-repo.zip"
        output.parent.mkdir()
        output.write_bytes(b"repo")
        return output

    async def stage(**kwargs):
        assert active == []
        return {"staged": True, **kwargs}

    monkeypatch.setattr(module, "_authoritative_repo_root", lambda: repo_root)
    monkeypatch.setattr(module, "_ensure_dev_repo_initialized_unlocked", initialize)
    monkeypatch.setattr(module, "acquire_global_workflow_lock", recording_lock)
    monkeypatch.setattr(managed, "build_dev_repo_zip", build)
    monkeypatch.setattr(bridge, "stage_dev_repo_zip", stage)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            module.repo_stage_current_dev_repo(repo_version="p2", verify=True)
        )
    finally:
        loop.close()

    assert result["ok"] is True
    assert calls == [None]


def test_managed_build_publish_and_repo_freeze_share_lock_before_network(
    tmp_path, monkeypatch
):
    import kodi_mcp_server.managed_addons as module

    active = []

    @contextlib.contextmanager
    def recording_lock(*, timeout=30.0, lock_root=None):
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    def build_publish(**kwargs):
        assert active
        return {
            "ok": True,
            "managed_addon_id": kwargs["managed_addon_id"],
            "build": {"version": "1.0.0"},
            "publish": {"action": "updated"},
        }

    def freeze(repo_version=None):
        assert active
        path = tmp_path / "addon" / "dev-repo.zip"
        path.parent.mkdir()
        path.write_bytes(b"repo")
        return path

    async def stage(**kwargs):
        assert active == []
        return {"staged": True}

    monkeypatch.setattr(module, "PROJECT_DIR", tmp_path / "project")
    monkeypatch.setattr(module, "acquire_global_workflow_lock", recording_lock)
    monkeypatch.setattr(module, "managed_addon_build_and_publish", build_publish)
    monkeypatch.setattr(module, "build_dev_repo_zip", freeze)
    monkeypatch.setattr(module, "stage_dev_repo_zip", stage)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            module.managed_addon_build_publish_and_stage(
                "plugin.test", "use_addon_xml", repo_version="p2"
            )
        )
    finally:
        loop.close()

    assert result["ok"] is True
    assert result["stage"] == {
        "repo_zip_path": str(tmp_path / "addon" / "dev-repo.zip"),
        "addon_stage_result": {"staged": True},
    }


def test_mcp_lock_timeout_preserves_stable_error_code_without_starting_mutation(
    monkeypatch,
):
    import kodi_mcp_mcp.server_core as server_core
    from kodi_mcp_server.orchestration_locking import OrchestrationLockTimeout
    from mcp.types import CallToolRequestParams

    mutation_started = False

    def locked_out(*, source_path):
        nonlocal mutation_started
        assert source_path == "/workspaces/plugin.test"
        raise OrchestrationLockTimeout("global_workflow")

    monkeypatch.setattr(server_core, "managed_addon_register", locked_out)
    server, _ = server_core.build_mcp_server(
        {"bridge": object(), "jsonrpc": object(), "notifications": None}
    )

    async def call():
        return await server.get_request_handler("tools/call").handler(
            None,
            CallToolRequestParams(
                name="managed_addon_register",
                arguments={"source_path": "/workspaces/plugin.test"},
            ),
        )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(call())
    finally:
        loop.close()

    envelope = json.loads(result.content[0].text)
    assert mutation_started is False
    assert result.is_error is True
    assert envelope["error_code"] == "ORCHESTRATION_LOCK_TIMEOUT"
    assert envelope["error_type"] == "resource_busy"
    assert envelope["error"] == "timed out waiting for global workflow lock"
    assert envelope["data"] is None
    assert envelope["raw"] is None
