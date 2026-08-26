"""Regression: an attempted-but-unverified addon apply must NOT be surfaced
as verified success.

Covers the single deterministic scenario supplied by the approved spec
(Follow-up Test #1, "Single-test implementation handoff"). The six direct
callees of managed_addon_build_publish_stage_and_apply are stubbed in the
kodi_mcp_server.kodi_apply module namespace; no production code is touched.
"""

import asyncio

import pytest

import kodi_mcp_server.kodi_apply as kodi_apply
from kodi_mcp_server.kodi_apply import managed_addon_build_publish_stage_and_apply


@pytest.mark.asyncio
async def test_install_attempted_but_unverified_is_not_verified_success(monkeypatch):
    """An install attempt whose observed version never reaches the target must
    land in the attempted-not-verified terminal state and must not be reported
    as ok/verified-success."""

    target_version = "2.0.0"

    # 1. build/publish/stage -> ok, sets target_version
    async def _stub_build_publish_stage(**kwargs):
        return {"ok": True, "build": {"version": target_version}}

    # 2. registry lookup -> sync, called without await
    def _stub_managed_addon_get(**kwargs):
        return {"ok": True, "managed_addon": {"addon_id": "plugin.test"}}

    # 3. refresh repo state -> ok
    async def _stub_refresh(**kwargs):
        return {
            "ok": True,
            "attempted": {"repo_refresh": True},
            "repo_refresh": {"error": None, "result": None},
        }

    # 4. repo-ready check -> bridge reachable, repo installed/enabled, addon visible
    async def _stub_repo_ready(**kwargs):
        return {
            "bridge_reachable": True,
            "repo_installed": True,
            "repo_enabled": True,
            "addon_visible_in_repo": True,
            "notes": [],
        }

    # 5. install-state read -> installed=False, version never equals target
    async def _stub_install_state(**kwargs):
        return {
            "ok": True,
            "addon_id": "plugin.test",
            "installed": False,
            "enabled": None,
            "version": None,
            "error": None,
            "raw": {},
        }

    # 6. install/update attempt -> install succeeded at the bridge level,
    #    but the installed version is NOT read back as the target
    async def _stub_install_or_update(**kwargs):
        return {
            "ok": True,
            "action": "install",
            "error": None,
            "result": {"success": True},
            "addon_before": {
                "ok": True,
                "addon_id": "plugin.test",
                "installed": False,
                "enabled": None,
                "version": None,
                "error": None,
                "raw": {},
            },
        }

    # Patch all six direct callees in the target module namespace.
    monkeypatch.setattr(kodi_apply, "managed_addon_build_publish_and_stage", _stub_build_publish_stage)
    monkeypatch.setattr(kodi_apply, "managed_addon_get", _stub_managed_addon_get)
    monkeypatch.setattr(kodi_apply, "kodi_refresh_dev_repo_state", _stub_refresh)
    monkeypatch.setattr(kodi_apply, "kodi_check_repo_ready_for_addon", _stub_repo_ready)
    monkeypatch.setattr(kodi_apply, "kodi_get_addon_install_state", _stub_install_state)
    monkeypatch.setattr(kodi_apply, "kodi_install_or_update_addon", _stub_install_or_update)

    # Optional local speedup: skip the 3x 1-second verification poll sleeps.
    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = await managed_addon_build_publish_stage_and_apply(
        managed_addon_id="plugin.test",
        version_policy="use_addon_xml",
        bridge_tool=object(),
        jsonrpc_tool=object(),
    )

    # The three required behavioral assertions:
    # an attempted-but-unverified apply is NOT surfaced as verified success.
    assert result["verification"]["apply_status"] == "install_attempted_not_verified"
    assert result["verification"]["apply_verified"] is False
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_update_attempted_but_unverified_is_not_verified_success(monkeypatch):
    """An UPDATE attempt whose observed version never reaches the target must
    land in the update attempted-not-verified terminal state and must not be
    reported as ok/verified-success.

    Distinct from the install-path scenario above: the addon is already
    installed (installed=True) at an older version, the bridge reports a
    successful update, but the post-attempt read-back never shows the target
    version, so the apply must surface as attempted-not-verified, not
    verified success.
    """

    target_version = "3.0.0"

    # 1. build/publish/stage -> ok, sets target_version
    async def _stub_build_publish_stage(**kwargs):
        return {"ok": True, "build": {"version": target_version}}

    # 2. registry lookup -> sync, called without await
    def _stub_managed_addon_get(**kwargs):
        return {"ok": True, "managed_addon": {"addon_id": "plugin.test"}}

    # 3. refresh repo state -> ok
    async def _stub_refresh(**kwargs):
        return {
            "ok": True,
            "attempted": {"repo_refresh": True},
            "repo_refresh": {"error": None, "result": None},
        }

    # 4. repo-ready check -> bridge reachable, repo installed/enabled, addon visible
    async def _stub_repo_ready(**kwargs):
        return {
            "bridge_reachable": True,
            "repo_installed": True,
            "repo_enabled": True,
            "addon_visible_in_repo": True,
            "notes": [],
        }

    # 5. install-state read -> addon is ALREADY installed (this is an update),
    #    at an older version than the target
    async def _stub_install_state(**kwargs):
        return {
            "ok": True,
            "addon_id": "plugin.test",
            "installed": True,
            "enabled": True,
            "version": "2.0.0",
            "error": None,
            "raw": {},
        }

    # 6. install/update attempt -> action="update", bridge-level success,
    #    but the installed version is NOT read back as the target
    async def _stub_install_or_update(**kwargs):
        return {
            "ok": True,
            "action": "update",
            "error": None,
            "result": {"success": True},
            "addon_before": {
                "ok": True,
                "addon_id": "plugin.test",
                "installed": True,
                "enabled": True,
                "version": "2.0.0",
                "error": None,
                "raw": {},
            },
        }

    # Patch all six direct callees in the target module namespace.
    monkeypatch.setattr(kodi_apply, "managed_addon_build_publish_and_stage", _stub_build_publish_stage)
    monkeypatch.setattr(kodi_apply, "managed_addon_get", _stub_managed_addon_get)
    monkeypatch.setattr(kodi_apply, "kodi_refresh_dev_repo_state", _stub_refresh)
    monkeypatch.setattr(kodi_apply, "kodi_check_repo_ready_for_addon", _stub_repo_ready)
    monkeypatch.setattr(kodi_apply, "kodi_get_addon_install_state", _stub_install_state)
    monkeypatch.setattr(kodi_apply, "kodi_install_or_update_addon", _stub_install_or_update)

    # Optional local speedup: skip the verification poll sleeps.
    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = await managed_addon_build_publish_stage_and_apply(
        managed_addon_id="plugin.test",
        version_policy="use_addon_xml",
        bridge_tool=object(),
        jsonrpc_tool=object(),
    )

    # The required behavioral assertions:
    # an attempted-but-unverified UPDATE is NOT surfaced as verified success.
    assert result["verification"]["apply_verified"] is False
    assert result["ok"] is False
    # Independently determined from source (kodi_apply.py: action == "update"
    # and post-attempt state not verified -> "update_attempted_not_verified").
    assert result["verification"]["apply_status"] == "update_attempted_not_verified"


@pytest.mark.asyncio
async def test_verified_install_is_reported_as_applied(monkeypatch):
    """A genuinely verified install (observed version reaches the target) MUST
    be surfaced as verified success: apply_status="applied",
    apply_verified=True, and overall ok=True.

    This is the false-negative complement of the two attempted-but-unverified
    tests above: they pin that an unverified apply is NOT reported as success;
    this pins that a verified apply IS reported as success. It exercises the
    decision-tree path where the post-attempt read-back equals the target and
    the before/after versions differ (None -> target), which neither existing
    test covers.
    """

    target_version = "2.0.0"

    # 1. build/publish/stage -> ok, sets target_version
    async def _stub_build_publish_stage(**kwargs):
        return {"ok": True, "build": {"version": target_version}}

    # 2. registry lookup -> sync, called without await
    def _stub_managed_addon_get(**kwargs):
        return {"ok": True, "managed_addon": {"addon_id": "plugin.test"}}

    # 3. refresh repo state -> ok
    async def _stub_refresh(**kwargs):
        return {
            "ok": True,
            "attempted": {"repo_refresh": True},
            "repo_refresh": {"error": None, "result": None},
        }

    # 4. repo-ready check -> bridge reachable, repo installed/enabled, addon visible
    async def _stub_repo_ready(**kwargs):
        return {
            "bridge_reachable": True,
            "repo_installed": True,
            "repo_enabled": True,
            "addon_visible_in_repo": True,
            "notes": [],
        }

    # 5. install-state read -> STATEFUL. First call (addon_before) reports the
    #    addon not yet installed at any version; second call (addon_after, the
    #    post-attempt read-back) reports it installed at the target version.
    #    This before/after divergence is what makes apply_verified True and
    #    drives the "applied" classification. The post-attempt value equals the
    #    target, so the bounded verification poll loop breaks on its first
    #    iteration without needing further reads.
    install_state_calls = {"n": 0}

    async def _stub_install_state(**kwargs):
        install_state_calls["n"] += 1
        if install_state_calls["n"] == 1:
            return {
                "ok": True,
                "addon_id": "plugin.test",
                "installed": False,
                "enabled": None,
                "version": None,
                "error": None,
                "raw": {},
            }
        return {
            "ok": True,
            "addon_id": "plugin.test",
            "installed": True,
            "enabled": True,
            "version": target_version,
            "error": None,
            "raw": {},
        }

    # 6. install attempt -> bridge-level success (action="install", installed
    #    was False before the attempt)
    async def _stub_install_or_update(**kwargs):
        return {
            "ok": True,
            "action": "install",
            "error": None,
            "result": {"success": True},
            "addon_before": {
                "ok": True,
                "addon_id": "plugin.test",
                "installed": False,
                "enabled": None,
                "version": None,
                "error": None,
                "raw": {},
            },
        }

    # Patch all six direct callees in the target module namespace.
    monkeypatch.setattr(kodi_apply, "managed_addon_build_publish_and_stage", _stub_build_publish_stage)
    monkeypatch.setattr(kodi_apply, "managed_addon_get", _stub_managed_addon_get)
    monkeypatch.setattr(kodi_apply, "kodi_refresh_dev_repo_state", _stub_refresh)
    monkeypatch.setattr(kodi_apply, "kodi_check_repo_ready_for_addon", _stub_repo_ready)
    monkeypatch.setattr(kodi_apply, "kodi_get_addon_install_state", _stub_install_state)
    monkeypatch.setattr(kodi_apply, "kodi_install_or_update_addon", _stub_install_or_update)

    # Optional local speedup: skip any verification poll sleeps.
    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = await managed_addon_build_publish_stage_and_apply(
        managed_addon_id="plugin.test",
        version_policy="use_addon_xml",
        bridge_tool=object(),
        jsonrpc_tool=object(),
    )

    # The verified-install contract: a confirmed apply IS surfaced as success.
    assert result["verification"]["apply_status"] == "applied"
    assert result["verification"]["apply_verified"] is True
    assert result["ok"] is True
