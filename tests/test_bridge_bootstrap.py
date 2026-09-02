from __future__ import annotations

import hashlib
import json
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.types import CallToolRequestParams

from kodi_mcp_mcp import server_core
from kodi_mcp_server import bootstrap_app, bridge_bootstrap
from kodi_mcp_server.bootstrap_app import configure_bootstrap_app
from kodi_mcp_server.bridge_bootstrap import (
    BootstrapBundleError,
    inspect_bootstrap_state,
    load_bootstrap_bundle,
    write_bootstrap_manifest,
)


ADDON_ID = "service.kodi_mcp"
VERSION = "0.2.36"
FINGERPRINT = "a" * 64
GIT_SHA = "b" * 40


def _write_bundle(tmp_path: Path, *, addon_id: str = ADDON_ID, version: str = VERSION) -> Path:
    artifact = tmp_path / f"{ADDON_ID}-{VERSION}.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{ADDON_ID}/addon.xml",
            f'<addon id="{addon_id}" name="Kodi MCP Service" version="{version}" provider-name="kodi_mcp" />',
        )
        archive.writestr(
            f"{ADDON_ID}/build_manifest.json",
            json.dumps(
                {
                    "source_git_sha": GIT_SHA,
                    "source_fingerprint_sha256": FINGERPRINT,
                }
            ),
        )
    manifest = {
        "schema_version": 1,
        "addon_id": ADDON_ID,
        "version": VERSION,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "source_git_sha": GIT_SHA,
        "source_fingerprint_sha256": FINGERPRINT,
    }
    manifest_path = tmp_path / "bridge-bootstrap.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _rewrite_bundle(
    manifest_path: Path,
    *,
    extra_members: list[tuple[str, bytes]] | None = None,
    addon_xml: bytes | None = None,
    build_manifest: bytes | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    member_compressions: dict[str, int] | None = None,
) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = manifest_path.parent / manifest["artifact"]
    members = [
        (
            f"{ADDON_ID}/addon.xml",
            addon_xml
            if addon_xml is not None
            else f'<addon id="{ADDON_ID}" name="Kodi MCP Service" version="{VERSION}" provider-name="kodi_mcp" />'.encode(),
        ),
        (
            f"{ADDON_ID}/build_manifest.json",
            build_manifest
            if build_manifest is not None
            else json.dumps(
                {
                    "source_git_sha": GIT_SHA,
                    "source_fingerprint_sha256": FINGERPRINT,
                }
            ).encode(),
        ),
        *(extra_members or []),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(artifact_path, "w", compression) as archive:
            for name, data in members:
                archive.writestr(
                    name,
                    data,
                    compress_type=(member_compressions or {}).get(name, compression),
                )
    manifest["artifact_sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return artifact_path


def _mutate_first_zip_member_metadata(
    manifest_path: Path,
    *,
    compression_method: int | None = None,
    encrypted: bool = False,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = manifest_path.parent / manifest["artifact"]
    data = bytearray(artifact_path.read_bytes())
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    if compression_method is not None:
        encoded = compression_method.to_bytes(2, "little")
        data[local + 8 : local + 10] = encoded
        data[central + 10 : central + 12] = encoded
    if encrypted:
        local_flags = int.from_bytes(data[local + 6 : local + 8], "little") | 1
        central_flags = int.from_bytes(data[central + 8 : central + 10], "little") | 1
        data[local + 6 : local + 8] = local_flags.to_bytes(2, "little")
        data[central + 8 : central + 10] = central_flags.to_bytes(2, "little")
    artifact_path.write_bytes(data)
    manifest["artifact_sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _response(*, result=None, error=None):
    return SimpleNamespace(result=result, error=error)


class _JsonRpc:
    def __init__(self, *, addon=None, version_error=None, details_error=None):
        self.addon = addon
        self.version_error = version_error
        self.details_error = details_error
        self.calls: list[str] = []

    async def get_jsonrpc_version(self):
        self.calls.append("get_jsonrpc_version")
        return _response(result={"version": {"major": 13}}, error=self.version_error)

    async def get_addon_details(self, addonid: str):
        assert addonid == ADDON_ID
        self.calls.append("get_addon_details")
        if self.details_error is not None:
            return _response(error=self.details_error)
        if self.addon is None:
            return _response(error="jsonrpc error -32602: Invalid params.")
        return _response(result={"addon": self.addon})


_DEFAULT_HEALTH = object()


class _Bridge:
    def __init__(self, *, health_error=None, health_result=_DEFAULT_HEALTH, status=None):
        self.health_error = health_error
        self.health_result = (
            {
                "status": "ok",
                "service": ADDON_ID,
                "addon_id": ADDON_ID,
                "health_type": "shallow",
            }
            if health_result is _DEFAULT_HEALTH
            else health_result
        )
        self.status = status or {}
        self.calls: list[str] = []

    async def get_bridge_health(self):
        self.calls.append("get_bridge_health")
        return _response(result=self.health_result, error=self.health_error)

    async def get_bridge_status(self):
        self.calls.append("get_bridge_status")
        return _response(result=self.status)


def _exact_status():
    return {
        "addon_id": ADDON_ID,
        "addon_version": VERSION,
        "build": {
            "source_git_sha": GIT_SHA,
            "source_fingerprint_sha256": FINGERPRINT,
        },
    }


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _artifact_response(manifest_path: Path):
    app = FastAPI()
    configure_bootstrap_app(
        app,
        manifest_path=manifest_path,
        base_url="https://mcp.example.test",
    )
    with TestClient(app) as client:
        return client.get("/bootstrap/service.kodi_mcp.zip")


def _assert_rejected_before_decompression(
    manifest_path: Path,
    monkeypatch,
    *,
    match: str,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("metadata rejection must precede open/testzip")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", forbidden)
    with pytest.raises(BootstrapBundleError, match=match):
        load_bootstrap_bundle(manifest_path)
    monkeypatch.undo()

    assert _artifact_response(manifest_path).status_code == 503


def test_bundle_validation_pins_authoritative_zip_identity(tmp_path: Path):
    bundle = load_bootstrap_bundle(_write_bundle(tmp_path))

    assert bundle.addon_id == ADDON_ID
    assert bundle.version == VERSION
    assert bundle.source_git_sha == GIT_SHA
    assert bundle.source_fingerprint_sha256 == FINGERPRINT
    assert bundle.artifact_sha256 == hashlib.sha256(bundle.artifact_path.read_bytes()).hexdigest()


def test_manifest_writer_converts_canonical_build_output_into_valid_bundle(tmp_path: Path):
    source_manifest = _write_bundle(tmp_path)
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    source_manifest.unlink()

    output = write_bootstrap_manifest(
        {
            "addon_id": source["addon_id"],
            "version": source["version"],
            "artifact": str(tmp_path / source["artifact"]),
            "artifact_sha256": source["artifact_sha256"],
            "head": source["source_git_sha"],
            "source_fingerprint_sha256": source["source_fingerprint_sha256"],
        },
        tmp_path / "bridge-bootstrap.json",
    )

    assert output == tmp_path / "bridge-bootstrap.json"
    assert load_bootstrap_bundle(output).version == VERSION


def test_manifest_writer_preserves_existing_good_manifest_on_failure(tmp_path: Path):
    output = _write_bundle(tmp_path)
    original = output.read_bytes()
    source = json.loads(output.read_text(encoding="utf-8"))

    with pytest.raises(BootstrapBundleError, match="addon id"):
        write_bootstrap_manifest(
            {
                "addon_id": "service.not_kodi_mcp",
                "version": source["version"],
                "artifact": str(tmp_path / source["artifact"]),
                "artifact_sha256": source["artifact_sha256"],
                "head": source["source_git_sha"],
                "source_fingerprint_sha256": source["source_fingerprint_sha256"],
            },
            output,
        )

    assert output.read_bytes() == original
    assert load_bootstrap_bundle(output).version == VERSION


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("hash", "SHA-256"),
        ("addon_id", "addon id"),
        ("version", "version"),
        ("fingerprint", "fingerprint"),
    ],
)
def test_bundle_validation_rejects_bad_or_mismatched_artifact(tmp_path: Path, mutation: str, match: str):
    manifest_path = _write_bundle(
        tmp_path,
        addon_id="service.not_kodi_mcp" if mutation == "addon_id" else ADDON_ID,
        version="9.9.9" if mutation == "version" else VERSION,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "hash":
        manifest["artifact_sha256"] = "0" * 64
    elif mutation == "fingerprint":
        manifest["source_fingerprint_sha256"] = "c" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BootstrapBundleError, match=match):
        load_bootstrap_bundle(manifest_path)


def test_bundle_validation_rejects_artifact_path_escape(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = "../outside.zip"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BootstrapBundleError, match="same directory"):
        load_bootstrap_bundle(manifest_path)


def test_bundle_validation_rejects_symlink_escape(tmp_path: Path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    manifest_path = _write_bundle(bundle_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inside = bundle_dir / manifest["artifact"]
    outside = tmp_path / "outside.zip"
    inside.replace(outside)
    inside.symlink_to(outside)

    with pytest.raises(BootstrapBundleError, match="same directory"):
        load_bootstrap_bundle(manifest_path)


def test_absent_bridge_returns_user_action_without_mutation(tmp_path: Path):
    jsonrpc = _JsonRpc(addon=None)
    bridge = _Bridge(health_error="must not be called")
    manifest_path = _write_bundle(tmp_path)

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=manifest_path,
            base_url="https://mcp.example.test",
        )
    )
    retry = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=manifest_path,
            base_url="https://mcp.example.test",
        )
    )

    assert result == retry
    assert result["state"] == "user_action_required"
    assert result["verified"] is False
    assert result["installed"] is False
    assert result["artifact"]["download_url"] == "https://mcp.example.test/bootstrap/service.kodi_mcp.zip"
    assert result["resume"]["tool"] == "bridge_bootstrap_status"
    assert "Install from zip" in " ".join(result["user_actions"])
    assert jsonrpc.calls == [
        "get_jsonrpc_version",
        "get_addon_details",
        "get_jsonrpc_version",
        "get_addon_details",
    ]
    assert bridge.calls == []


def test_mcp_bootstrap_status_remains_available_when_bridge_is_absent(
    tmp_path: Path, monkeypatch
):
    jsonrpc = _JsonRpc(addon=None)
    bridge = _Bridge(health_error="must not be called")
    monkeypatch.setattr(server_core, "BRIDGE_BOOTSTRAP_MANIFEST_PATH", _write_bundle(tmp_path))
    monkeypatch.setattr(server_core, "REPO_BASE_URL", "https://mcp.example.test")
    server, _ = server_core.build_mcp_server(
        {"jsonrpc": jsonrpc, "bridge": bridge, "notifications": None}
    )

    async def call_tool():
        return await server.get_request_handler("tools/call").handler(
            None,
            CallToolRequestParams(name="bridge_bootstrap_status", arguments={}),
        )

    result = _run(call_tool())
    envelope = json.loads(result.content[0].text)

    assert result.is_error is False
    assert envelope["ok"] is True
    assert envelope["data"]["state"] == "user_action_required"
    assert envelope["data"]["verified"] is False
    assert bridge.calls == []


def test_missing_bundle_is_unsupported_and_does_not_probe_or_mutate(tmp_path: Path):
    jsonrpc = _JsonRpc(addon=None)
    bridge = _Bridge()

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=tmp_path / "missing.json",
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "bootstrap_unsupported"
    assert result["verified"] is False
    assert "manifest" in result["reason"]
    assert jsonrpc.calls == []
    assert bridge.calls == []


def test_jsonrpc_prerequisite_failure_is_unsupported(tmp_path: Path):
    jsonrpc = _JsonRpc(addon=None, version_error="connection refused")
    bridge = _Bridge()

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=_write_bundle(tmp_path),
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "bootstrap_unsupported"
    assert result["reason"] == "Kodi JSON-RPC is unavailable: connection refused"
    assert jsonrpc.calls == ["get_jsonrpc_version"]
    assert bridge.calls == []


def test_addon_probe_failure_is_not_misreported_as_absent(tmp_path: Path):
    jsonrpc = _JsonRpc(details_error="jsonrpc error -32000: Internal error")
    bridge = _Bridge()

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=_write_bundle(tmp_path),
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "bootstrap_unsupported"
    assert "addon state could not be determined" in result["reason"]
    assert bridge.calls == []


def test_installed_bridge_that_has_not_started_requires_user_action(tmp_path: Path):
    jsonrpc = _JsonRpc(addon={"addonid": ADDON_ID, "version": VERSION, "enabled": True})
    bridge = _Bridge(health_error="connection refused")

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=_write_bundle(tmp_path),
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "user_action_required"
    assert result["installed"] is True
    assert result["verified"] is False
    assert "shared token" in " ".join(result["user_actions"])
    assert bridge.calls == ["get_bridge_health"]


@pytest.mark.parametrize(
    "health_result",
    [
        None,
        [],
        {},
        {"status": "error", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow"},
        {"status": "degraded", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow"},
        {"status": True, "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow"},
        {"status": "ok", "service": ADDON_ID, "health_type": "shallow"},
        {"status": "ok", "service": ADDON_ID, "addon_id": "service.other", "health_type": "shallow"},
        {"status": "ok", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "unexpected"},
        {"status": "ok", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow", "healthy": False},
        {"status": "ok", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow", "ready": False},
        {"status": "ok", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow", "ok": False},
        {"status": "ok", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "deep", "kodi_jsonrpc_ok": False},
    ],
)
def test_negative_or_malformed_bridge_health_fails_closed(tmp_path: Path, health_result):
    jsonrpc = _JsonRpc(addon={"addonid": ADDON_ID, "version": VERSION, "enabled": True})
    bridge = _Bridge(health_result=health_result, status=_exact_status())

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=_write_bundle(tmp_path),
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "user_action_required"
    assert result["verified"] is False
    assert result["next_stage"] is None
    assert "health" in result["reason"].lower()
    assert bridge.calls == ["get_bridge_health"]


@pytest.mark.parametrize(
    "health_result",
    [
        {"status": "ok", "service": ADDON_ID, "addon_id": ADDON_ID, "health_type": "shallow"},
        {
            "status": "ok",
            "service": ADDON_ID,
            "addon_id": ADDON_ID,
            "health_type": "deep",
            "kodi_jsonrpc_ok": True,
        },
    ],
)
def test_affirmative_bridge_health_and_exact_identity_succeeds(tmp_path: Path, health_result):
    jsonrpc = _JsonRpc(addon={"addonid": ADDON_ID, "version": VERSION, "enabled": True})
    bridge = _Bridge(health_result=health_result, status=_exact_status())

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=_write_bundle(tmp_path),
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "already_installed"
    assert result["verified"] is True
    assert result["next_stage"] == "managed_deployment"
    assert bridge.calls == ["get_bridge_health", "get_bridge_status"]


def test_wrong_post_install_identity_is_never_success(tmp_path: Path):
    jsonrpc = _JsonRpc(addon={"addonid": ADDON_ID, "version": "0.2.35", "enabled": True})
    status = _exact_status()
    status["addon_version"] = "0.2.35"
    status["build"]["source_fingerprint_sha256"] = "d" * 64
    bridge = _Bridge(status=status)

    result = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=_write_bundle(tmp_path),
            base_url="https://mcp.example.test",
        )
    )

    assert result["state"] == "already_installed"
    assert result["verified"] is False
    assert result["next_stage"] == "authoritative_update"
    assert "kodi_addon_version" in result["identity_mismatches"]
    assert "bridge_version" in result["identity_mismatches"]
    assert "source_fingerprint_sha256" in result["identity_mismatches"]


def test_success_transitions_to_existing_managed_path_and_is_idempotent(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    jsonrpc = _JsonRpc(addon=None)
    bridge = _Bridge(status=_exact_status())

    first = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=manifest_path,
            base_url="https://mcp.example.test",
        )
    )
    jsonrpc.addon = {"addonid": ADDON_ID, "version": VERSION, "enabled": True}
    second = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=manifest_path,
            base_url="https://mcp.example.test",
        )
    )
    third = _run(
        inspect_bootstrap_state(
            jsonrpc_tool=jsonrpc,
            bridge_tool=bridge,
            manifest_path=manifest_path,
            base_url="https://mcp.example.test",
        )
    )

    assert first["state"] == "user_action_required"
    assert second["state"] == third["state"] == "already_installed"
    assert second["verified"] is third["verified"] is True
    assert second["next_stage"] == third["next_stage"] == "managed_deployment"
    assert second["identity_mismatches"] == third["identity_mismatches"] == []


@pytest.mark.parametrize(
    "compression",
    [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA],
    ids=["bzip2", "lzma"],
)
def test_zip_policy_rejects_forbidden_codec_before_decompression(
    tmp_path: Path, monkeypatch, compression: int
):
    manifest_path = _write_bundle(tmp_path)
    forbidden_name = f"{ADDON_ID}/forbidden-codec.bin"
    _rewrite_bundle(
        manifest_path,
        extra_members=[(forbidden_name, b"codec payload")],
        member_compressions={forbidden_name: compression},
    )

    _assert_rejected_before_decompression(
        manifest_path,
        monkeypatch,
        match="compression method",
    )


def test_zip_policy_rejects_unknown_codec_before_decompression(
    tmp_path: Path, monkeypatch
):
    manifest_path = _write_bundle(tmp_path)
    _mutate_first_zip_member_metadata(manifest_path, compression_method=99)

    _assert_rejected_before_decompression(
        manifest_path,
        monkeypatch,
        match="compression method",
    )


def test_zip_policy_rejects_encrypted_member_before_decompression(
    tmp_path: Path, monkeypatch
):
    manifest_path = _write_bundle(tmp_path)
    _mutate_first_zip_member_metadata(manifest_path, encrypted=True)

    _assert_rejected_before_decompression(
        manifest_path,
        monkeypatch,
        match="encrypted members",
    )


@pytest.mark.parametrize(
    "compression",
    [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED],
    ids=["stored", "deflated"],
)
def test_zip_policy_accepts_supported_codec(tmp_path: Path, compression: int):
    manifest_path = _write_bundle(tmp_path)
    _rewrite_bundle(manifest_path, compression=compression)

    assert load_bootstrap_bundle(manifest_path).artifact_bytes


def test_zip_policy_accepts_mixed_stored_and_deflated_members(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    addon_xml_name = f"{ADDON_ID}/addon.xml"
    build_manifest_name = f"{ADDON_ID}/build_manifest.json"
    stored_name = f"{ADDON_ID}/stored.txt"
    deflated_name = f"{ADDON_ID}/deflated.txt"
    _rewrite_bundle(
        manifest_path,
        extra_members=[(stored_name, b"stored"), (deflated_name, b"deflated")],
        member_compressions={
            addon_xml_name: zipfile.ZIP_DEFLATED,
            build_manifest_name: zipfile.ZIP_STORED,
            stored_name: zipfile.ZIP_STORED,
            deflated_name: zipfile.ZIP_DEFLATED,
        },
    )

    assert load_bootstrap_bundle(manifest_path).artifact_bytes


def test_zip_policy_rejects_excessive_member_count_before_member_reads(
    tmp_path: Path, monkeypatch
):
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    manifest_path = _write_bundle(tmp_path)
    extras = [
        (f"{ADDON_ID}/empty-{index}.txt", b"")
        for index in range(policy.max_members - 1)
    ]
    _rewrite_bundle(manifest_path, extra_members=extras)

    def forbidden_open(*args, **kwargs):
        raise AssertionError("member content must not be opened before member-count rejection")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden_open)
    with pytest.raises(BootstrapBundleError, match="member count"):
        load_bootstrap_bundle(manifest_path)
    monkeypatch.undo()

    response = _artifact_response(manifest_path)
    assert response.status_code == 503


def test_zip_policy_accepts_member_count_at_limit(tmp_path: Path):
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    manifest_path = _write_bundle(tmp_path)
    extras = [
        (f"{ADDON_ID}/empty-{index}.txt", b"")
        for index in range(policy.max_members - 2)
    ]
    _rewrite_bundle(manifest_path, extra_members=extras)

    assert len(load_bootstrap_bundle(manifest_path).artifact_bytes) > 0


def test_zip_policy_rejects_oversized_single_member(tmp_path: Path):
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    manifest_path = _write_bundle(tmp_path)
    _rewrite_bundle(
        manifest_path,
        extra_members=[
            (f"{ADDON_ID}/oversized.bin", b"x" * (policy.max_member_uncompressed_bytes + 1))
        ],
    )

    response = _artifact_response(manifest_path)

    assert response.status_code == 503
    assert "member uncompressed size" in response.json()["detail"]


def test_zip_policy_rejects_excessive_aggregate_uncompressed_size(tmp_path: Path):
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    manifest_path = _write_bundle(tmp_path)
    chunk_size = policy.max_member_uncompressed_bytes - 1
    chunk_count = policy.max_total_uncompressed_bytes // chunk_size + 1
    _rewrite_bundle(
        manifest_path,
        extra_members=[
            (f"{ADDON_ID}/aggregate-{index}.bin", bytes([index % 251]) * chunk_size)
            for index in range(chunk_count)
        ],
        compression=zipfile.ZIP_STORED,
    )

    response = _artifact_response(manifest_path)

    assert response.status_code == 503
    assert "aggregate uncompressed size" in response.json()["detail"]


def test_zip_policy_rejects_extreme_member_expansion_ratio(tmp_path: Path):
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    manifest_path = _write_bundle(tmp_path)
    _rewrite_bundle(
        manifest_path,
        extra_members=[
            (f"{ADDON_ID}/high-ratio.bin", b"x" * min(1_048_576, policy.max_member_uncompressed_bytes))
        ],
    )

    response = _artifact_response(manifest_path)

    assert response.status_code == 503
    assert "expansion ratio" in response.json()["detail"]


def test_zip_policy_rejects_oversized_required_member_before_read(tmp_path: Path):
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    manifest_path = _write_bundle(tmp_path)
    addon_xml = (
        f'<addon id="{ADDON_ID}" name="Kodi MCP Service" version="{VERSION}" provider-name="kodi_mcp" />'.encode()
        + b" " * policy.max_required_member_bytes
    )
    _rewrite_bundle(
        manifest_path,
        addon_xml=addon_xml,
        compression=zipfile.ZIP_STORED,
    )

    response = _artifact_response(manifest_path)

    assert response.status_code == 503
    assert "required member" in response.json()["detail"]


@pytest.mark.parametrize(
    "duplicate_name",
    [
        f"{ADDON_ID}/addon.xml",
        f"{ADDON_ID.upper()}/ADDON.XML",
    ],
)
def test_zip_policy_rejects_duplicate_or_case_ambiguous_identity_member(
    tmp_path: Path, duplicate_name: str
):
    manifest_path = _write_bundle(tmp_path)
    _rewrite_bundle(
        manifest_path,
        extra_members=[
            (
                duplicate_name,
                f'<addon id="{ADDON_ID}" name="Duplicate" version="{VERSION}" />'.encode(),
            )
        ],
    )

    response = _artifact_response(manifest_path)

    assert response.status_code == 503
    assert "duplicate or case-ambiguous" in response.json()["detail"]


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "/absolute.bin",
        "../traversal.bin",
        "C:/drive-absolute.bin",
        "folder\\backslash.bin",
    ],
)
def test_zip_policy_rejects_unsafe_member_paths(tmp_path: Path, unsafe_name: str):
    manifest_path = _write_bundle(tmp_path)
    _rewrite_bundle(manifest_path, extra_members=[(unsafe_name, b"x")])

    response = _artifact_response(manifest_path)

    assert response.status_code == 503
    assert "unsafe member path" in response.json()["detail"]


@pytest.mark.parametrize(
    ("file_size", "compress_size", "match"),
    [
        (-1, 0, "impossible member sizes"),
        (1, -1, "impossible member sizes"),
        (1, 0, "expansion ratio is unbounded"),
    ],
)
def test_zip_policy_rejects_impossible_or_zero_compressed_metadata(
    file_size: int, compress_size: int, match: str
):
    info = SimpleNamespace(
        filename=f"{ADDON_ID}/pathological.bin",
        file_size=file_size,
        compress_size=compress_size,
        compress_type=zipfile.ZIP_STORED,
        flag_bits=0,
    )
    archive = SimpleNamespace(infolist=lambda: [info])

    with pytest.raises(BootstrapBundleError, match=match):
        bridge_bootstrap._validate_zip_metadata(
            cast(zipfile.ZipFile, archive),
            (f"{ADDON_ID}/addon.xml", f"{ADDON_ID}/build_manifest.json"),
        )


def test_zip_policy_rejects_declared_aggregate_compressed_work_over_artifact_bound():
    policy = bridge_bootstrap.BOOTSTRAP_ZIP_POLICY
    infos = [
        SimpleNamespace(
            filename=f"{ADDON_ID}/overlap-{index}.bin",
            file_size=1,
            compress_size=policy.max_artifact_bytes // 2 + 1,
            compress_type=zipfile.ZIP_STORED,
            flag_bits=0,
        )
        for index in range(2)
    ]
    archive = SimpleNamespace(infolist=lambda: infos)

    with pytest.raises(BootstrapBundleError, match="aggregate compressed size"):
        bridge_bootstrap._validate_zip_metadata(
            cast(zipfile.ZipFile, archive),
            (f"{ADDON_ID}/addon.xml", f"{ADDON_ID}/build_manifest.json"),
        )


def test_bootstrap_http_serves_only_validated_exact_artifact(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    bundle = load_bootstrap_bundle(manifest_path)
    app = FastAPI()
    configure_bootstrap_app(app, manifest_path=manifest_path, base_url="https://mcp.example.test")

    with TestClient(app) as client:
        index_response = client.get("/bootstrap/")
        manifest_response = client.get("/bootstrap/manifest.json")
        artifact_head = client.head("/bootstrap/service.kodi_mcp.zip")
        artifact_response = client.get("/bootstrap/service.kodi_mcp.zip")

    assert index_response.status_code == 200
    assert 'href="service.kodi_mcp.zip"' in index_response.text
    assert manifest_response.status_code == 200
    assert artifact_head.status_code == 200
    assert artifact_head.headers["content-length"] == str(len(bundle.artifact_bytes))
    assert manifest_response.json()["artifact_sha256"] == bundle.artifact_sha256
    assert manifest_response.json()["download_url"] == "https://mcp.example.test/bootstrap/service.kodi_mcp.zip"
    assert artifact_response.status_code == 200
    assert artifact_response.content == bundle.artifact_bytes
    assert hashlib.sha256(artifact_response.content).hexdigest() == bundle.artifact_sha256
    assert artifact_response.headers["content-disposition"].endswith(f'filename="{ADDON_ID}-{VERSION}.zip"')


def test_bootstrap_http_serves_validated_bytes_despite_path_replacement(
    tmp_path: Path, monkeypatch
):
    manifest_path = _write_bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = tmp_path / manifest["artifact"]
    original_bytes = artifact_path.read_bytes()
    original_loader = bootstrap_app.load_bootstrap_bundle

    def load_then_replace(path):
        bundle = original_loader(path)
        artifact_path.unlink()
        artifact_path.symlink_to(tmp_path / "replacement.zip")
        (tmp_path / "replacement.zip").write_bytes(b"unvalidated replacement")
        return bundle

    monkeypatch.setattr(bootstrap_app, "load_bootstrap_bundle", load_then_replace)
    app = FastAPI()
    configure_bootstrap_app(app, manifest_path=manifest_path, base_url="https://mcp.example.test")

    with TestClient(app) as client:
        response = client.get("/bootstrap/service.kodi_mcp.zip")

    assert response.status_code == 200
    assert response.content == original_bytes
    assert hashlib.sha256(response.content).hexdigest() == manifest["artifact_sha256"]


def test_bootstrap_http_rejects_oversize_artifact(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = tmp_path / manifest["artifact"]
    artifact_path.write_bytes(b"x" * (bridge_bootstrap.MAX_BOOTSTRAP_ARTIFACT_BYTES + 1))
    manifest["artifact_sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    app = FastAPI()
    configure_bootstrap_app(app, manifest_path=manifest_path, base_url="https://mcp.example.test")

    with TestClient(app) as client:
        response = client.get("/bootstrap/service.kodi_mcp.zip")

    assert response.status_code == 503
    assert "maximum" in response.json()["detail"]


def test_bootstrap_http_rejects_malformed_artifact_with_matching_hash(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = tmp_path / manifest["artifact"]
    artifact_path.write_bytes(b"not a zip")
    manifest["artifact_sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    app = FastAPI()
    configure_bootstrap_app(app, manifest_path=manifest_path, base_url="https://mcp.example.test")

    with TestClient(app) as client:
        response = client.get("/bootstrap/service.kodi_mcp.zip")

    assert response.status_code == 503
    assert "invalid" in response.json()["detail"]


def test_bootstrap_http_fails_closed_for_invalid_artifact(tmp_path: Path):
    manifest_path = _write_bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    app = FastAPI()
    configure_bootstrap_app(app, manifest_path=manifest_path, base_url="https://mcp.example.test")

    with TestClient(app) as client:
        response = client.get("/bootstrap/service.kodi_mcp.zip")

    assert response.status_code == 503
    assert "SHA-256" in response.json()["detail"]
