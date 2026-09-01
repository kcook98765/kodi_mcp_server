from __future__ import annotations

import asyncio

import pytest

from kodi_mcp_server.bridge_deployment import (
    BridgeDeploymentMismatch,
    DeploymentObservation,
    deploy_expected_bridge,
    verify_expected_bridge,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_expected_bridge_verification_rejects_stale_running_build():
    observation = DeploymentObservation(
        bridge_version="0.2.33",
        addon_version="0.2.33",
        bridge_healthy=True,
        jsonrpc_healthy=True,
        gui_state_ok=True,
    )

    with pytest.raises(BridgeDeploymentMismatch, match="expected 0.2.34"):
        verify_expected_bridge("0.2.34", observation)


def test_expected_bridge_verification_rejects_wrong_build_fingerprint():
    observation = DeploymentObservation(
        bridge_version="0.2.34",
        addon_version="0.2.34",
        bridge_healthy=True,
        jsonrpc_healthy=True,
        gui_state_ok=True,
        build_fingerprint="a" * 64,
    )

    with pytest.raises(BridgeDeploymentMismatch, match="build fingerprint"):
        verify_expected_bridge(
            "0.2.34",
            observation,
            expected_build_fingerprint="b" * 64,
        )


def test_deployment_contract_upgrades_an_existing_older_bridge():
    state = {"version": "0.2.33"}
    installs = 0

    async def probe():
        return DeploymentObservation(
            bridge_version=state["version"],
            addon_version=state["version"],
            bridge_healthy=True,
            jsonrpc_healthy=True,
            gui_state_ok=True,
        )

    async def install():
        nonlocal installs
        installs += 1
        state["version"] = "0.2.34"

    result = _run(deploy_expected_bridge("0.2.34", probe=probe, install=install))

    assert result.action == "upgrade"
    assert result.before.bridge_version == "0.2.33"
    assert result.after.bridge_version == "0.2.34"
    assert installs == 1


def test_deployment_contract_bootstraps_when_bridge_is_absent():
    state: dict[str, str | None] = {"version": None}

    async def probe():
        version = state["version"]
        return DeploymentObservation(
            bridge_version=version,
            addon_version=version,
            bridge_healthy=version is not None,
            jsonrpc_healthy=True,
            gui_state_ok=version is not None,
        )

    async def install():
        state["version"] = "0.2.34"

    result = _run(deploy_expected_bridge("0.2.34", probe=probe, install=install))

    assert result.action == "bootstrap"
    assert result.before.bridge_version is None
    assert result.after.bridge_version == "0.2.34"
