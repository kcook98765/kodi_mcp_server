#!/usr/bin/env python3
"""Deploy the authoritative Kodi MCP bridge build to a selected lab target.

This is development/test fixture preparation. It deliberately keeps Kodi lab
selection and GUI-driven ZIP installation out of the MCP product API.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from kodi_mcp_server.addon_xml import read_addon_id_and_version
from kodi_mcp_server.bridge_deployment import (
    BridgeDeploymentMismatch,
    DeploymentObservation,
    deploy_expected_bridge,
    verify_expected_bridge,
)
from kodi_mcp_server.composition import build_bridge_tool, build_jsonrpc_tool
from kodi_mcp_server.dev_loop_artifacts import inspect_addon_zip
from kodi_mcp_server.managed_addons import build_addon_zip_from_source

ADDON_ID = "service.kodi_mcp"
DEFAULT_SOURCE = Path("/workspaces/kodi_mcp_addon")
DEFAULT_LABCTL = Path("/home/kyle/kodi_mcp_test_lab/labctl")
DEFAULT_EVIDENCE_DIR = Path("/workspaces/.stage/kodi-mcp-remediation/evidence")


def _run(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files(source: Path) -> list[Path]:
    excluded_dirs = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache"}
    return sorted(
        (
            path
            for path in source.rglob("*")
            if path.is_file()
            and not excluded_dirs.intersection(path.relative_to(source).parts)
            and path.suffix.lower() not in {".pyc", ".pyo"}
        ),
        key=lambda path: path.relative_to(source).as_posix(),
    )


def _source_fingerprint(source: Path) -> str:
    digest = hashlib.sha256()
    for path in _source_files(source):
        digest.update(path.relative_to(source).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def prepare_build(source: Path) -> dict[str, Any]:
    source = source.resolve()
    addon_id, version = read_addon_id_and_version(source / "addon.xml")
    if addon_id != ADDON_ID:
        raise ValueError(f"authoritative source addon id is {addon_id!r}, expected {ADDON_ID!r}")
    if not version or version == "0.0.0":
        raise ValueError(f"authoritative source version is invalid: {version!r}")

    head = _git(source, "rev-parse", "HEAD")
    branch = _git(source, "branch", "--show-current")
    status = _git(source, "status", "--porcelain")
    fingerprint = _source_fingerprint(source)

    staging = Path(tempfile.mkdtemp(prefix="kodi-mcp-bridge-build-")) / ADDON_ID
    try:
        shutil.copytree(
            source,
            staging,
            ignore=shutil.ignore_patterns(
                ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", "*.pyc", "*.pyo"
            ),
        )
        manifest_path = staging / "build_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "source_git_sha": head,
                    "source_fingerprint_sha256": fingerprint,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(manifest_path, (315532800, 315532800))
        artifact = build_addon_zip_from_source(staging, addon_id, version)
    finally:
        shutil.rmtree(staging.parent, ignore_errors=True)

    validation = inspect_addon_zip(path=artifact, addon_id=addon_id, version=version)
    return {
        "repository": str(source),
        "branch": branch,
        "head": head,
        "git_status": status,
        "source_fingerprint_sha256": fingerprint,
        "addon_id": addon_id,
        "version": version,
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "artifact_validation": validation,
    }


def _result_dict(response: Any) -> dict[str, Any]:
    result = getattr(response, "result", None)
    return result if isinstance(result, dict) else {}


async def _setting(jsonrpc: Any, setting: str) -> bool:
    response = await jsonrpc.execute_jsonrpc(
        "Settings.GetSettingValue", {"setting": setting}
    )
    if response.error:
        raise RuntimeError(f"failed to read Kodi setting {setting}: {response.error}")
    return bool(_result_dict(response).get("value"))


async def _gui_state(bridge: Any) -> dict[str, Any]:
    response = await bridge.gui_state()
    if response.error:
        raise RuntimeError(f"failed to read GUI state: {response.error}")
    return _result_dict(response)


def _control_name(state: dict[str, Any]) -> str:
    return str(state.get("current_control") or "").strip().strip("[]")


async def _select_control(
    bridge: Any,
    target: str,
    *,
    moves: int = 100,
) -> None:
    target_folded = target.casefold()
    seen: list[str] = []
    for _ in range(moves):
        state = await _gui_state(bridge)
        current = _control_name(state)
        seen.append(current)
        if current.casefold() == target_folded:
            selected = await bridge.gui_action("select")
            if selected.error:
                raise RuntimeError(f"failed to select {target!r}: {selected.error}")
            await asyncio.sleep(0.6)
            return
        moved = await bridge.gui_action("down")
        if moved.error:
            raise RuntimeError(f"failed while navigating to {target!r}: {moved.error}")
        await asyncio.sleep(0.25)
    raise RuntimeError(f"GUI control {target!r} not found; observed {seen}")


async def _accept_yes_no(bridge: Any) -> None:
    state = await _gui_state(bridge)
    if state.get("current_window") != "Yes / No dialog":
        return
    current = _control_name(state)
    if current.casefold() == "no":
        moved = await bridge.gui_action("left")
        if moved.error:
            raise RuntimeError(f"failed to choose Yes: {moved.error}")
        await asyncio.sleep(0.2)
        state = await _gui_state(bridge)
        current = _control_name(state)
    if current.casefold() != "yes":
        raise RuntimeError(f"unexpected confirmation control: {current!r}")
    selected = await bridge.gui_action("select")
    if selected.error:
        raise RuntimeError(f"failed to accept confirmation: {selected.error}")
    await asyncio.sleep(0.7)


async def _dismiss_ok_dialog(bridge: Any) -> None:
    state = await _gui_state(bridge)
    if state.get("current_window") == "OK dialog" and _control_name(state).casefold() == "ok":
        selected = await bridge.gui_action("select")
        if selected.error:
            raise RuntimeError(f"failed to dismiss OK dialog: {selected.error}")
        await asyncio.sleep(0.5)


async def _set_bool_setting(
    jsonrpc: Any,
    bridge: Any,
    setting: str,
    value: bool,
    *,
    confirm_warning: bool = False,
) -> None:
    request = jsonrpc.execute_jsonrpc(
        "Settings.SetSettingValue", {"setting": setting, "value": value}
    )
    if confirm_warning:
        task = asyncio.create_task(request)
        for _ in range(50):
            state = await _gui_state(bridge)
            if state.get("current_window") == "Yes / No dialog":
                await _accept_yes_no(bridge)
                break
            if task.done():
                break
            await asyncio.sleep(0.1)
        response = await task
    else:
        response = await request
    if response.error or response.result is not True:
        raise RuntimeError(f"failed to set Kodi setting {setting}: {response.error or response.result}")
    await asyncio.sleep(0.4)
    if await _setting(jsonrpc, setting) is not value:
        raise RuntimeError(f"Kodi setting {setting} did not become {value}")


async def _install_uploaded_zip(
    *,
    artifact: Path,
    bridge: Any,
    jsonrpc: Any,
) -> None:
    upload = await bridge.upload_bridge_addon_zip(str(artifact))
    if upload.error:
        raise RuntimeError(f"bridge artifact upload failed: {upload.error}")
    upload_result = _result_dict(upload)
    if upload_result.get("size_bytes") != artifact.stat().st_size:
        raise RuntimeError("bridge artifact upload size did not match source artifact")

    unknown_sources_before = await _setting(jsonrpc, "addons.unknownsources")
    show_hidden_before = await _setting(jsonrpc, "filelists.showhidden")
    try:
        if not unknown_sources_before:
            await _set_bool_setting(
                jsonrpc,
                bridge,
                "addons.unknownsources",
                True,
                confirm_warning=True,
            )
        if not show_hidden_before:
            await _set_bool_setting(jsonrpc, bridge, "filelists.showhidden", True)

        activated = await jsonrpc.execute_jsonrpc(
            "GUI.ActivateWindow", {"window": "addonbrowser"}
        )
        if activated.error:
            raise RuntimeError(f"failed to open Add-on browser: {activated.error}")
        await asyncio.sleep(0.8)
        await _dismiss_ok_dialog(bridge)
        await _select_control(bridge, "Install from zip file", moves=10)
        await _accept_yes_no(bridge)

        for control in (
            "Home folder",
            ".kodi",
            "userdata",
            "addon_data",
            ADDON_ID,
            "uploads",
            artifact.name,
        ):
            await _select_control(bridge, control)

        # Selecting the bridge artifact updates and restarts the service. The
        # version/health contract performs the authoritative convergence check.
        await asyncio.sleep(2)
    finally:
        # Restore fixture-only browsing/security settings after Kodi has loaded
        # the new bridge. Build fresh clients because the bridge restarted.
        fresh_bridge = build_bridge_tool()
        fresh_jsonrpc = build_jsonrpc_tool()
        if not show_hidden_before:
            await _set_bool_setting(fresh_jsonrpc, fresh_bridge, "filelists.showhidden", False)
        if not unknown_sources_before:
            await _set_bool_setting(fresh_jsonrpc, fresh_bridge, "addons.unknownsources", False)


async def _probe_direct() -> DeploymentObservation:
    bridge = build_bridge_tool()
    jsonrpc = build_jsonrpc_tool()

    status = await bridge.get_bridge_status()
    addon = await jsonrpc.get_addon_details(ADDON_ID)
    ping = await jsonrpc.execute_jsonrpc("JSONRPC.Ping")
    gui = await bridge.gui_state()

    status_result = _result_dict(status)
    bridge_version = status_result.get("addon_version")
    build_payload = status_result.get("build")
    build_fingerprint = (
        build_payload.get("source_fingerprint_sha256")
        if isinstance(build_payload, dict)
        else None
    )
    addon_payload = _result_dict(addon).get("addon")
    addon_version = addon_payload.get("version") if isinstance(addon_payload, dict) else None
    return DeploymentObservation(
        bridge_version=str(bridge_version) if bridge_version else None,
        addon_version=str(addon_version) if addon_version else None,
        bridge_healthy=status.error is None,
        jsonrpc_healthy=ping.error is None and ping.result == "pong",
        gui_state_ok=gui.error is None and bool(_result_dict(gui).get("ok")),
        build_fingerprint=build_fingerprint,
    )


async def _mcp_call(url: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            response = await session.call_tool(name, arguments)
    wire = response.model_dump(mode="json")
    texts = [item.get("text") for item in wire.get("content", []) if item.get("type") == "text"]
    envelope = json.loads(texts[0]) if len(texts) == 1 else None
    return {"wire_is_error": wire.get("isError"), "envelope": envelope}


async def _mcp_acceptance(
    url: str,
    expected_version: str,
    expected_build_fingerprint: str,
) -> dict[str, Any]:
    names = {
        "bridge_status": {},
        "addon_details": {"addonid": ADDON_ID},
        "kodi_status": {},
        "kodi_gui_state": {},
    }
    results = {name: await _mcp_call(url, name, args) for name, args in names.items()}
    envelopes = {name: result["envelope"] for name, result in results.items()}
    if any(not isinstance(value, dict) or value.get("ok") is not True for value in envelopes.values()):
        raise BridgeDeploymentMismatch("one or more final MCP acceptance calls failed")

    bridge_data = envelopes["bridge_status"]["data"]
    bridge_version = bridge_data.get("addon_version")
    bridge_build = bridge_data.get("build")
    build_fingerprint = (
        bridge_build.get("source_fingerprint_sha256")
        if isinstance(bridge_build, dict)
        else None
    )
    addon_version = envelopes["addon_details"]["data"].get("addon", {}).get("version")
    status_data = envelopes["kodi_status"]["data"]
    observation = DeploymentObservation(
        bridge_version=bridge_version,
        addon_version=addon_version,
        bridge_healthy=status_data.get("bridge", {}).get("status") == "ok",
        jsonrpc_healthy=status_data.get("jsonrpc", {}).get("status") == "ok",
        gui_state_ok=envelopes["kodi_gui_state"]["data"].get("ok") is True,
        build_fingerprint=build_fingerprint,
    )
    verify_expected_bridge(
        expected_version,
        observation,
        expected_build_fingerprint=expected_build_fingerprint,
    )
    return {"observation": observation.to_dict(), "calls": results}


def _server_status(mcp_url: str) -> dict[str, Any]:
    base = mcp_url.split("/mcp", 1)[0]
    with urllib.request.urlopen(f"{base}/status", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    build = prepare_build(args.source)
    expected_version = build["version"]
    record: dict[str, Any] = {
        "evidence_class": {
            "selection": "LAB / WHITE-BOX",
            "deployment": "DIRECT / DEPLOYMENT",
            "acceptance": "MCP / BLACK-BOX",
        },
        "build": build,
    }

    if args.kodi:
        record["lab_select"] = _run([str(args.labctl), "select", args.kodi], cwd=args.labctl.parent)
        record["lab_health_before"] = _run([str(args.labctl), "health", args.kodi], cwd=args.labctl.parent)

    server_status = _server_status(args.mcp_url)
    identity = server_status.get("server", {}).get("identity")
    if not isinstance(identity, dict) or identity.get("version") == "0.0.0":
        raise RuntimeError("running MCP server does not expose a valid runtime identity")
    if identity.get("running_files_match_current_source") is not True:
        raise RuntimeError("running MCP server source fingerprint does not match current source files")
    record["server_status_before"] = server_status

    bridge = build_bridge_tool()
    jsonrpc = build_jsonrpc_tool()

    async def install() -> None:
        await _install_uploaded_zip(
            artifact=Path(build["artifact"]),
            bridge=bridge,
            jsonrpc=jsonrpc,
        )

    result = await deploy_expected_bridge(
        expected_version,
        probe=_probe_direct,
        install=install,
        attempts=45,
        poll_interval_seconds=1,
        expected_build_fingerprint=build["source_fingerprint_sha256"],
    )
    record["deployment"] = result.to_dict()
    home = await build_bridge_tool().gui_action("home")
    if home.error:
        raise RuntimeError(f"failed to restore Kodi Home after deployment: {home.error}")
    await asyncio.sleep(0.5)
    await _dismiss_ok_dialog(build_bridge_tool())
    record["mcp_acceptance"] = await _mcp_acceptance(
        args.mcp_url,
        expected_version,
        build["source_fingerprint_sha256"],
    )

    if args.kodi:
        record["lab_health_after"] = _run([str(args.labctl), "health", args.kodi], cwd=args.labctl.parent)
    record["server_status_after"] = _server_status(args.mcp_url)
    return record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kodi", choices=("19", "20", "21", "22"))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--labctl", type=Path, default=DEFAULT_LABCTL)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8010/mcp/")
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    return parser


def main() -> int:
    args = _parser().parse_args()
    started = int(time.time())
    try:
        record = asyncio.run(run(args))
        record["ok"] = True
    except Exception as exc:
        record = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "kodi": args.kodi,
        }
    record["started_at"] = started
    record["finished_at"] = int(time.time())

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    suffix = args.kodi or "active"
    evidence_path = args.evidence_dir / f"bridge-deployment-kodi{suffix}.json"
    evidence_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"evidence": str(evidence_path), **record}, indent=2, sort_keys=True))
    return 0 if record.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
