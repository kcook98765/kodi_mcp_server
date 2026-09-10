"""Phase 4B-P0 managed-addon validation characterization tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
import kodi_mcp_mcp.tool_contract as tool_contract
import kodi_mcp_server.managed_addons as managed_addons
from kodi_mcp_mcp.output_contracts import output_schema_for
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.milestone_a_bridge import _parse_envelope
from kodi_mcp_server.models.messages import ResponseMessage


class _HealthBridge:
    def __init__(self, response: ResponseMessage | Exception) -> None:
        self.response = response
        self.calls = 0

    async def get_bridge_health(self) -> ResponseMessage:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _StateClient:
    def __init__(self, response: ResponseMessage | Exception) -> None:
        self.response = response
        self.calls = 0

    async def mcp_state(self) -> ResponseMessage:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Tripwire:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected access: {name}")


def _state_response(
    *,
    transport_ok: bool = True,
    registration_present: bool | None = True,
    registration_stale: bool | None = False,
    repo_zip_file_exists: bool | None = True,
    repo_zip_special_path: str | None = "special://home/addons/packages/dev-repo.zip",
    dev_setup_available: bool | None = True,
    error: str | None = None,
) -> ResponseMessage:
    derived = {
        "registration_present": registration_present,
        "registration_stale": registration_stale,
        "repo_zip_file_exists": repo_zip_file_exists,
        "dev_setup_available": dev_setup_available,
    }
    result: dict[str, Any] = {
        "transport": {"ok": transport_ok},
        "result": {"ok": transport_ok, "derived": derived},
    }
    if repo_zip_special_path is not None:
        result["result"]["repo_zip"] = {"special_path": repo_zip_special_path}
    return ResponseMessage(request_id="state", result=result, error=error)


def _filesystem_snapshot(root: Path) -> list[tuple[str, str, bytes | None]]:
    if not root.exists():
        return []
    snapshot = []
    for path in sorted(root.rglob("*")):
        snapshot.append(
            (
                str(path.relative_to(root)),
                "dir" if path.is_dir() else "file",
                path.read_bytes() if path.is_file() else None,
            )
        )
    return snapshot


async def _call(server: Any, arguments: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    result = await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(
            name="managed_addon_validate_state",
            arguments=arguments,
        ),
    )
    envelope = json.loads(result.content[0].text)
    assert result.structured_content == envelope
    return result, envelope


def _expected_validation(
    *,
    managed_addon_id: str,
    entry: dict[str, Any] | None,
    repo_root: Path,
    reachable: bool,
    bridge_error: str | None,
    mcp_state_read_ok: bool,
    registration_present: bool | None,
    registration_stale: bool | None,
    repo_zip_file_exists: bool | None,
    repo_zip_special_path: str | None,
    dev_setup_available: bool | None,
) -> dict[str, Any]:
    last_build = entry.get("last_build") if isinstance(entry, dict) else None
    registry_exists = isinstance(entry, dict)
    enabled = bool(entry.get("enabled", False)) if isinstance(entry, dict) else False
    source_path = str(entry.get("source_path") or "") if isinstance(entry, dict) else ""
    addon_id = str(entry.get("addon_id") or "") if isinstance(entry, dict) else ""
    last_observed_version = (
        str(entry.get("last_observed_version") or "") if isinstance(entry, dict) else ""
    )

    def exists(value: str) -> bool:
        try:
            return bool(value and Path(value).exists())
        except Exception:
            return False

    last_build_zip_exists = (
        exists(str((last_build or {}).get("zip_path") or ""))
        if isinstance(last_build, dict)
        else False
    )
    published_repo_zip_exists = (
        exists(str((last_build or {}).get("repo_zip_path") or ""))
        if isinstance(last_build, dict)
        else False
    )
    dev_repo = repo_root / "dev-repo"
    dev_repo_exists = dev_repo.exists()
    addons_xml_exists = (dev_repo / "addons.xml").exists()
    addons_xml_md5_exists = (dev_repo / "addons.xml.md5").exists()
    ready_for_build = bool(
        registry_exists
        and enabled
        and source_path
        and exists(source_path)
        and exists(str(Path(source_path) / "addon.xml"))
    )
    ready_for_publish = bool(
        ready_for_build and last_build_zip_exists and dev_repo_exists
    )
    ready_for_stage = bool(
        ready_for_publish
        and reachable
        and mcp_state_read_ok
        and bool(registration_present)
        and not bool(registration_stale)
    )
    ready_for_kodi_install = bool(
        ready_for_stage
        and bool(repo_zip_file_exists)
        and bool(dev_setup_available)
    )
    return {
        "ok": True,
        "managed_addon_id": managed_addon_id,
        "registry": {
            "exists": registry_exists,
            "enabled": enabled,
            "addon_id": addon_id,
            "source_path": source_path,
            "last_observed_version": last_observed_version,
            "last_build": last_build if isinstance(last_build, dict) else None,
        },
        "artifacts": {
            "last_build_zip_exists": last_build_zip_exists,
            "published_repo_zip_exists": published_repo_zip_exists,
            "dev_repo_exists": dev_repo_exists,
            "addons_xml_exists": addons_xml_exists,
            "addons_xml_md5_exists": addons_xml_md5_exists,
        },
        "kodi_bridge": {
            "reachable": reachable,
            "mcp_state_read_ok": mcp_state_read_ok,
            "error": bridge_error,
            "registration_present": registration_present,
            "registration_stale": registration_stale,
            "repo_zip_file_exists": repo_zip_file_exists,
            "repo_zip_special_path": repo_zip_special_path,
            "dev_setup_available": dev_setup_available,
        },
        "summary": {
            "ready_for_build": ready_for_build,
            "ready_for_publish": ready_for_publish,
            "ready_for_stage": ready_for_stage,
            "ready_for_kodi_install": ready_for_kodi_install,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "scenario",
        "registry_present",
        "source_dir",
        "addon_xml",
        "build_zip",
        "dev_repo",
        "published_zip",
        "health_error",
        "state_response",
        "expected_summary",
        "expected_state_calls",
    ),
    [
        (
            "absent registry record",
            False,
            False,
            False,
            False,
            False,
            False,
            None,
            _state_response(),
            (False, False, False, False),
            1,
        ),
        (
            "partial local state",
            True,
            True,
            False,
            False,
            False,
            False,
            None,
            _state_response(),
            (False, False, False, False),
            1,
        ),
        (
            "complete local state",
            True,
            True,
            True,
            True,
            True,
            True,
            None,
            _state_response(registration_present=False),
            (True, True, False, False),
            1,
        ),
        (
            "fully ready state",
            True,
            True,
            True,
            True,
            True,
            True,
            None,
            _state_response(),
            (True, True, True, True),
            1,
        ),
        (
            "bridge health failure",
            True,
            True,
            True,
            True,
            True,
            True,
            "health unavailable",
            _state_response(),
            (True, True, False, False),
            0,
        ),
        (
            "mcp state transport failure",
            True,
            True,
            True,
            True,
            True,
            True,
            None,
            _state_response(
                transport_ok=False,
                registration_present=None,
                registration_stale=None,
                repo_zip_file_exists=None,
                repo_zip_special_path=None,
                dev_setup_available=None,
            ),
            (True, True, False, False),
            1,
        ),
        (
            "stale registration",
            True,
            True,
            True,
            True,
            True,
            True,
            None,
            _state_response(registration_stale=True),
            (True, True, False, False),
            1,
        ),
        (
            "staged repo missing",
            True,
            True,
            True,
            True,
            True,
            True,
            None,
            _state_response(repo_zip_file_exists=False),
            (True, True, True, False),
            1,
        ),
        (
            "staged repo present",
            True,
            True,
            True,
            True,
            True,
            True,
            None,
            _state_response(repo_zip_file_exists=True),
            (True, True, True, True),
            1,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
async def test_dispatch_characterization_preserves_exact_validation_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    registry_present: bool,
    source_dir: bool,
    addon_xml: bool,
    build_zip: bool,
    dev_repo: bool,
    published_zip: bool,
    health_error: str | None,
    state_response: ResponseMessage,
    expected_summary: tuple[bool, bool, bool, bool],
    expected_state_calls: int,
) -> None:
    del scenario
    managed_addon_id = "plugin.example"
    source = tmp_path / "source"
    build = tmp_path / "artifacts" / "plugin.example-1.2.3.zip"
    published = tmp_path / "repo" / "dev-repo" / "plugin.example" / build.name
    repo_root = tmp_path / "repo"
    if source_dir:
        source.mkdir(parents=True)
    if addon_xml:
        (source / "addon.xml").write_text("<addon id='plugin.example'/>", encoding="utf-8")
    if build_zip:
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_bytes(b"build")
    if dev_repo:
        (repo_root / "dev-repo").mkdir(parents=True, exist_ok=True)
        (repo_root / "dev-repo" / "addons.xml").write_text("<addons/>", encoding="utf-8")
        (repo_root / "dev-repo" / "addons.xml.md5").write_text("fixture", encoding="utf-8")
    if published_zip:
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_bytes(b"published")

    entry = (
        {
            "managed_addon_id": managed_addon_id,
            "addon_id": managed_addon_id,
            "source_path": str(source),
            "enabled": True,
            "last_observed_version": "1.2.3",
            "last_build": {
                "version": "1.2.3",
                "zip_path": str(build),
                "repo_zip_path": str(published),
            },
        }
        if registry_present
        else None
    )
    original_entry = deepcopy(entry)
    registry_calls = 0

    def registry_get(*, managed_addon_id: str) -> dict[str, Any]:
        nonlocal registry_calls
        registry_calls += 1
        if entry is None:
            return {
                "ok": False,
                "error_code": "NOT_FOUND",
                "managed_addon_id": managed_addon_id,
            }
        return {"ok": True, "managed_addon": entry}

    def registry_write_tripwire(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected registry write: {args!r} {kwargs!r}")

    health_response = ResponseMessage(
        request_id="health",
        result={"status": "ok"},
        error=health_error,
    )
    health_bridge = _HealthBridge(health_response)
    state_client = _StateClient(state_response)

    async def legacy_read_addon_state() -> tuple[Any, ResponseMessage]:
        response = await state_client.mcp_state()
        return _parse_envelope(response.result), response

    monkeypatch.setattr(server_core, "managed_addon_get", registry_get)
    monkeypatch.setattr(server_core, "AUTHORITATIVE_REPO_ROOT", repo_root)
    monkeypatch.setattr(
        server_core,
        "read_addon_state",
        legacy_read_addon_state,
        raising=False,
    )
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: state_client,
        raising=False,
    )
    monkeypatch.setattr(
        managed_addons,
        "save_managed_registry",
        registry_write_tripwire,
    )

    before = _filesystem_snapshot(tmp_path)
    server, _ = build_mcp_server(
        {
            "bridge": health_bridge,
            "jsonrpc": _Tripwire(),
            "notifications": _Tripwire(),
            "registry": _Tripwire(),
            "transport_pool": _Tripwire(),
        }
    )
    result, envelope = await _call(
        server,
        {"managed_addon_id": managed_addon_id},
    )

    reachable = health_error is None
    if reachable:
        payload = state_response.result or {}
        transport = payload.get("transport")
        mcp_state_read_ok = bool(
            isinstance(transport, dict) and transport.get("ok") is True
        )
        result_obj = payload.get("result") if state_response.error is None else None
        derived = result_obj.get("derived") if isinstance(result_obj, dict) else None
        repo_zip = result_obj.get("repo_zip") if isinstance(result_obj, dict) else None
        registration_present = (
            derived.get("registration_present") if isinstance(derived, dict) else None
        )
        registration_stale = (
            derived.get("registration_stale") if isinstance(derived, dict) else None
        )
        repo_zip_file_exists = (
            derived.get("repo_zip_file_exists") if isinstance(derived, dict) else None
        )
        dev_setup_available = (
            derived.get("dev_setup_available") if isinstance(derived, dict) else None
        )
        special_path = repo_zip.get("special_path") if isinstance(repo_zip, dict) else None
        repo_zip_special_path = (
            special_path.strip()
            if isinstance(special_path, str) and special_path.strip()
            else None
        )
    else:
        mcp_state_read_ok = False
        registration_present = None
        registration_stale = None
        repo_zip_file_exists = None
        repo_zip_special_path = None
        dev_setup_available = None

    expected = _expected_validation(
        managed_addon_id=managed_addon_id,
        entry=entry,
        repo_root=repo_root,
        reachable=reachable,
        bridge_error=health_error,
        mcp_state_read_ok=mcp_state_read_ok,
        registration_present=registration_present,
        registration_stale=registration_stale,
        repo_zip_file_exists=repo_zip_file_exists,
        repo_zip_special_path=repo_zip_special_path,
        dev_setup_available=dev_setup_available,
    )
    assert result.is_error is False
    assert envelope["ok"] is True
    assert envelope["data"] == expected
    assert envelope["raw"] == expected
    assert tuple(expected["summary"].values()) == expected_summary
    assert "target" not in envelope
    assert "target_id" not in envelope["raw"]
    assert registry_calls == 1
    assert health_bridge.calls == 1
    assert state_client.calls == expected_state_calls
    assert entry == original_entry
    assert _filesystem_snapshot(tmp_path) == before


@pytest.mark.asyncio
async def test_invalid_schema_precedes_registry_filesystem_and_bridge_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected access: {args!r} {kwargs!r}")

    monkeypatch.setattr(server_core, "managed_addon_get", tripwire)
    monkeypatch.setattr(server_core, "read_addon_state", tripwire, raising=False)
    monkeypatch.setattr(server_core, "build_bridge_client", tripwire, raising=False)
    server, _ = build_mcp_server(
        {
            "bridge": _Tripwire(),
            "jsonrpc": _Tripwire(),
            "notifications": _Tripwire(),
        }
    )

    result, envelope = await _call(server, {"managed_addon_id": "Plugin/Bad"})

    assert result.is_error is True
    assert envelope["ok"] is False
    assert envelope["error_type"] == "invalid_params"
    assert envelope["data"] is None


@pytest.mark.asyncio
async def test_unexpected_registry_exception_keeps_outer_unknown_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def registry_failure(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(server_core, "managed_addon_get", registry_failure)
    server, _ = build_mcp_server(
        {
            "bridge": _Tripwire(),
            "jsonrpc": _Tripwire(),
            "notifications": _Tripwire(),
        }
    )

    result, envelope = await _call(
        server,
        {"managed_addon_id": "plugin.example"},
    )

    assert result.is_error is True
    assert envelope["ok"] is False
    assert envelope["error"] == "request failed: registry exploded"
    assert envelope["error_type"] == "unknown_error"
    assert envelope["raw"] is None


@pytest.mark.asyncio
async def test_external_contract_inventory_and_phase_4a_closure_are_unchanged() -> None:
    server, _ = build_mcp_server(
        {
            "bridge": _Tripwire(),
            "jsonrpc": _Tripwire(),
            "notifications": _Tripwire(),
        }
    )
    listed = await server.get_request_handler("tools/list").handler(None, None)
    by_name = {tool.name: tool for tool in listed.tools}
    targeted = {
        name
        for name, tool in by_name.items()
        if "target" in tool.input_schema.get("properties", {})
    }
    tool = by_name["managed_addon_validate_state"]

    assert len(by_name) == 56
    assert len(targeted) == 36
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert "managed_addon_validate_state" not in targeted
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "managed_addon_id": {
                "type": "string",
                "minLength": 1,
                "pattern": r"^(?=.*[a-z0-9])[a-z0-9._@-]+$",
            }
        },
        "required": ["managed_addon_id"],
        "additionalProperties": False,
    }
    assert tool.output_schema == output_schema_for("managed_addon_validate_state")
    assert tool.annotations is not None
    assert tool.annotations.model_dump(by_alias=True, exclude_none=False) == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": None,
        "idempotentHint": None,
        "openWorldHint": False,
    }
    assert not hasattr(tool_contract, "BATCH_D6_TARGET_TOOL_NAMES")


def test_helper_signature_and_static_dependency_isolation() -> None:
    import ast
    import inspect

    import kodi_mcp_server.managed_addon_validation as validation

    helper = validation.validate_managed_addon_state
    signature = inspect.signature(helper)
    assert list(signature.parameters) == [
        "managed_addon_id",
        "managed_addon_record",
        "authoritative_repo_root",
        "health_bridge_tool",
        "state_bridge_tool",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    source = inspect.getsource(validation)
    for forbidden in (
        "managed_addon_get",
        "build_bridge_client",
        "read_addon_state",
        "AUTHORITATIVE_REPO_ROOT",
        "runtime[",
        "os.environ",
    ):
        assert forbidden not in source

    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module.endswith(
            (
                ".config",
                ".managed_addons",
                ".milestone_a_bridge",
                ".targets.registry",
                ".targets.runtime",
                ".targets.transport_pool",
            )
        )
        for module in imported_modules
    )


@pytest.mark.asyncio
async def test_helper_accepts_distinct_injected_bridges_and_does_not_mutate_inputs(
    tmp_path: Path,
) -> None:
    from types import MappingProxyType

    from kodi_mcp_server.managed_addon_validation import validate_managed_addon_state

    source = tmp_path / "source"
    source.mkdir()
    (source / "addon.xml").write_text("<addon/>", encoding="utf-8")
    build_zip = tmp_path / "build.zip"
    build_zip.write_bytes(b"zip")
    dev_repo = tmp_path / "repo" / "dev-repo"
    dev_repo.mkdir(parents=True)
    entry = {
        "addon_id": "plugin.example",
        "source_path": str(source),
        "enabled": True,
        "last_observed_version": "1.0.0",
        "last_build": {"version": "1.0.0", "zip_path": str(build_zip)},
    }
    original = deepcopy(entry)
    health_bridge = _HealthBridge(
        ResponseMessage(request_id="health", result={"status": "ok"}, error=None)
    )

    class StateBridge:
        def __init__(self) -> None:
            self.calls = 0

        async def get_mcp_state(self) -> ResponseMessage:
            self.calls += 1
            return _state_response()

    state_bridge = StateBridge()
    before = _filesystem_snapshot(tmp_path)

    result = await validate_managed_addon_state(
        managed_addon_id="plugin.example",
        managed_addon_record=MappingProxyType(entry),
        authoritative_repo_root=tmp_path / "repo",
        health_bridge_tool=health_bridge,
        state_bridge_tool=state_bridge,
    )

    assert result["summary"] == {
        "ready_for_build": True,
        "ready_for_publish": True,
        "ready_for_stage": True,
        "ready_for_kodi_install": True,
    }
    assert health_bridge.calls == 1
    assert state_bridge.calls == 1
    assert entry == original
    assert _filesystem_snapshot(tmp_path) == before


@pytest.mark.asyncio
async def test_helper_accepts_same_bridge_for_both_roles_and_sequences_health_first(
    tmp_path: Path,
) -> None:
    from kodi_mcp_server.managed_addon_validation import validate_managed_addon_state

    events: list[str] = []

    class CombinedBridge:
        async def get_bridge_health(self) -> ResponseMessage:
            events.append("health")
            return ResponseMessage(
                request_id="health",
                result={"status": "ok"},
                error=None,
            )

        async def get_mcp_state(self) -> ResponseMessage:
            events.append("state")
            return _state_response()

    bridge = CombinedBridge()
    result = await validate_managed_addon_state(
        managed_addon_id="plugin.missing",
        managed_addon_record=None,
        authoritative_repo_root=tmp_path / "repo",
        health_bridge_tool=bridge,
        state_bridge_tool=bridge,
    )

    assert result["registry"]["exists"] is False
    assert events == ["health", "state"]


@pytest.mark.asyncio
async def test_helper_preserves_malformed_truthy_state_response_diagnostic(
    tmp_path: Path,
) -> None:
    from kodi_mcp_server.managed_addon_validation import validate_managed_addon_state

    health_bridge = _HealthBridge(
        ResponseMessage(request_id="health", result={"status": "ok"}, error=None)
    )

    class StateBridge:
        async def get_mcp_state(self) -> ResponseMessage:
            return ResponseMessage(
                request_id="state",
                result=["malformed"],  # type: ignore[arg-type]
                error=None,
            )

    result = await validate_managed_addon_state(
        managed_addon_id="plugin.missing",
        managed_addon_record=None,
        authoritative_repo_root=tmp_path / "repo",
        health_bridge_tool=health_bridge,
        state_bridge_tool=StateBridge(),  # type: ignore[arg-type]
    )

    assert result["kodi_bridge"]["mcp_state_read_ok"] is False
    assert result["kodi_bridge"]["error"] == "'list' object has no attribute 'get'"


@pytest.mark.asyncio
async def test_bridge_tool_get_mcp_state_delegates_once_and_returns_identity() -> None:
    from kodi_mcp_server.tools.bridge import BridgeTool

    response = _state_response()
    client = _StateClient(response)
    tool = BridgeTool(client)  # type: ignore[arg-type]

    actual = await tool.get_mcp_state()

    assert actual is response
    assert client.calls == 1


@pytest.mark.asyncio
async def test_dispatcher_wires_registry_snapshot_and_dual_bridge_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    registry_calls = 0
    entry = {
        "addon_id": "plugin.example",
        "source_path": str(tmp_path / "source"),
        "enabled": True,
    }
    state_client = _StateClient(_state_response())
    health_bridge = _HealthBridge(
        ResponseMessage(request_id="health", result={"status": "ok"}, error=None)
    )

    def registry_get(*, managed_addon_id: str) -> dict[str, Any]:
        nonlocal registry_calls
        registry_calls += 1
        assert managed_addon_id == "plugin.example"
        return {"ok": True, "managed_addon": entry}

    async def helper_spy(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "ok": True,
            "managed_addon_id": kwargs["managed_addon_id"],
            "registry": {},
            "artifacts": {},
            "kodi_bridge": {},
            "summary": {
                "ready_for_build": False,
                "ready_for_publish": False,
                "ready_for_stage": False,
                "ready_for_kodi_install": False,
            },
        }

    async def legacy_read_addon_state() -> tuple[Any, ResponseMessage]:
        response = await state_client.mcp_state()
        return _parse_envelope(response.result), response

    monkeypatch.setattr(server_core, "managed_addon_get", registry_get)
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: state_client,
        raising=False,
    )
    monkeypatch.setattr(
        server_core,
        "read_addon_state",
        legacy_read_addon_state,
        raising=False,
    )
    monkeypatch.setattr(
        server_core,
        "validate_managed_addon_state",
        helper_spy,
        raising=False,
    )
    monkeypatch.setattr(server_core, "AUTHORITATIVE_REPO_ROOT", tmp_path / "repo")
    server, _ = build_mcp_server(
        {
            "bridge": health_bridge,
            "jsonrpc": _Tripwire(),
            "notifications": _Tripwire(),
        }
    )

    result, envelope = await _call(
        server,
        {"managed_addon_id": "plugin.example"},
    )

    assert result.is_error is False
    assert envelope["data"]["managed_addon_id"] == "plugin.example"
    assert registry_calls == 1
    assert captured["managed_addon_id"] == "plugin.example"
    assert captured["managed_addon_record"] == entry
    assert captured["authoritative_repo_root"] == tmp_path / "repo"
    assert captured["health_bridge_tool"] is health_bridge
    assert captured["state_bridge_tool"].client is state_client
