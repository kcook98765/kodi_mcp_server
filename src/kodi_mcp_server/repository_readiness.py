"""Read-only readiness aggregation for the configured Kodi repository addon."""

from __future__ import annotations

import uuid

from .config import REPO_BASE_URL
from .models.messages import ErrorType, ResponseMessage
from .repository_addon_manifest import load_repository_addon_manifest


async def inspect_repository_readiness(bridge_tool) -> ResponseMessage:
    """Compare canonical server identity with fixed Kodi-side bridge evidence."""

    request_id = str(uuid.uuid4())
    response = await bridge_tool.get_repository_readiness()
    if response.error:
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error=response.error,
            error_type=response.error_type,
            error_code=response.error_code,
        )

    envelope = response.result
    if not isinstance(envelope, dict) or (envelope.get("transport") or {}).get("ok") is not True:
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error="repository readiness returned an invalid bridge envelope",
            error_type=ErrorType.INVALID_RESPONSE,
        )
    evidence = envelope.get("result")
    if not isinstance(evidence, dict) or evidence.get("ok") is not True:
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error=(evidence or {}).get("error", "repository readiness failed")
            if isinstance(evidence, dict)
            else "repository readiness returned an invalid result",
            error_type=ErrorType.INVALID_RESPONSE,
        )

    manifest = load_repository_addon_manifest()
    configured_identity = evidence.get("configured_identity") or {}
    urls = evidence.get("urls") or {}
    base_url = REPO_BASE_URL.rstrip("/")
    expected_urls = {
        "metadata": base_url + "/repo/content/addons.xml",
        "checksum": base_url + "/repo/content/addons.xml.md5",
        "datadir": base_url + "/repo/content/zips/",
    }
    identity_match = bool(
        evidence.get("installed")
        and evidence.get("addon_id") == manifest.addon_id
        and evidence.get("installed_version") == manifest.version
        and configured_identity.get("addon_id") == manifest.addon_id
        and configured_identity.get("version") == manifest.version
    )
    urls_match = all(urls.get(key) == value for key, value in expected_urls.items())
    metadata = evidence.get("metadata") or {}
    checksum = evidence.get("checksum") or {}
    package = evidence.get("package") or {}
    ready = bool(
        evidence.get("installed")
        and evidence.get("enabled")
        and identity_match
        and urls_match
        and metadata.get("reachable")
        and metadata.get("parseable")
        and checksum.get("reachable")
        and checksum.get("match")
        and package.get("observable")
        and package.get("reachable")
    )

    return ResponseMessage(
        request_id=request_id,
        result={
            "repository_id": manifest.addon_id,
            "installed": bool(evidence.get("installed")),
            "enabled": bool(evidence.get("enabled")),
            "installed_version": evidence.get("installed_version"),
            "canonical_version": manifest.version,
            "identity_match": identity_match,
            "urls": urls,
            "expected_urls": expected_urls,
            "urls_match_server_configuration": urls_match,
            "metadata": metadata,
            "checksum": checksum,
            "package": package,
            "catalog_refresh": evidence.get("catalog_refresh"),
            "catalog_ingestion": evidence.get("catalog_ingestion"),
            "ready": ready,
            "limitations": [
                "Kodi catalog refresh completion is not observable through a stable read-only API",
                "Kodi addon-database timestamps and entries are best-effort internal evidence and do not prove freshness",
            ],
        },
        error=None,
    )
