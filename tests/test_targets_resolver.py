import pytest

from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry
from kodi_mcp_server.targets.resolver import TargetNotFoundError, resolve_target


def _registry():
    return TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default:8080/jsonrpc",
            bridge_url="http://default:8765",
        ),
        targets_json="""[
            {
                "id": "secondary",
                "name": "Secondary",
                "endpoints": {
                    "jsonrpc_url": "http://secondary:8080/jsonrpc",
                    "bridge_url": "http://secondary:8765"
                }
            }
        ]""",
    )


def test_resolver_uses_default_when_no_explicit_target_is_supplied():
    assert resolve_target(_registry()).target_id == "default"


def test_resolver_returns_explicit_target_without_mutating_registry_default():
    registry = _registry()

    assert resolve_target(registry, "secondary").target_id == "secondary"
    assert resolve_target(registry).target_id == "default"


def test_resolver_reports_unknown_target_without_fallback():
    with pytest.raises(TargetNotFoundError, match="missing"):
        resolve_target(_registry(), "missing")
