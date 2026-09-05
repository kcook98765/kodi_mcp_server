"""Bounded installation orchestration for the canonical Kodi MCP repository addon."""

from __future__ import annotations

import hashlib
import stat
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .config import REPO_BASE_URL
from .models.messages import ErrorType, ResponseMessage
from .repo_generator import build_repo_addon
from .repository_addon_manifest import load_repository_addon_manifest

_REPOSITORY_MANIFEST = load_repository_addon_manifest()
# Compatibility exports are derived views, not independent version authority.
REPOSITORY_ADDON_ID = _REPOSITORY_MANIFEST.addon_id
REPOSITORY_ADDON_VERSION = _REPOSITORY_MANIFEST.version
REPOSITORY_STAGE_ID = "dev-repo"
REPOSITORY_MEMBERS = frozenset(
    {
        f"{REPOSITORY_ADDON_ID}/addon.xml",
        f"{REPOSITORY_ADDON_ID}/service.py",
        f"{REPOSITORY_ADDON_ID}/addons.xml",
    }
)


class RepositoryBootstrapError(ValueError):
    """The generated repository bootstrap failed its closed validation policy."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_repository_bootstrap_zip(
    path: str | Path, *, expected_sha256: str | None = None
) -> dict[str, object]:
    """Validate the exact generated canonical repository.kodi-mcp ZIP."""

    artifact = Path(path)
    if not artifact.is_file():
        raise RepositoryBootstrapError("canonical repository bootstrap artifact is missing")
    actual_sha256 = _sha256(artifact)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise RepositoryBootstrapError("canonical repository bootstrap SHA-256 mismatch")

    try:
        with zipfile.ZipFile(artifact, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != REPOSITORY_MEMBERS:
                raise RepositoryBootstrapError("canonical repository bootstrap ZIP layout mismatch")
            for info in infos:
                mode = (info.external_attr >> 16) & 0xFFFF
                if info.is_dir() or stat.S_ISLNK(mode):
                    raise RepositoryBootstrapError("canonical repository bootstrap has unsafe members")
            addon_xml = archive.read(f"{REPOSITORY_ADDON_ID}/addon.xml")
    except RepositoryBootstrapError:
        raise
    except Exception as exc:
        raise RepositoryBootstrapError(f"canonical repository bootstrap ZIP is invalid: {exc}") from exc

    try:
        root = ElementTree.fromstring(addon_xml)
    except Exception as exc:
        raise RepositoryBootstrapError(f"canonical repository addon.xml is invalid: {exc}") from exc
    if root.tag != "addon" or root.get("id") != REPOSITORY_ADDON_ID:
        raise RepositoryBootstrapError("canonical repository addon id mismatch")
    if root.get("version") != REPOSITORY_ADDON_VERSION:
        raise RepositoryBootstrapError("canonical repository addon version mismatch")
    if not any(
        node.tag == "extension"
        and node.get("point") == _REPOSITORY_MANIFEST.repository_extension
        for node in list(root)
    ):
        raise RepositoryBootstrapError("canonical repository extension is missing")

    return {
        "addon_id": REPOSITORY_ADDON_ID,
        "version": REPOSITORY_ADDON_VERSION,
        "sha256": actual_sha256,
        "size_bytes": artifact.stat().st_size,
        "path": str(artifact),
    }


def _business_result(response: ResponseMessage, operation: str) -> dict[str, object]:
    if response.error:
        raise RepositoryBootstrapError(f"{operation} failed: {response.error}")
    envelope = response.result
    if not isinstance(envelope, dict) or envelope.get("transport", {}).get("ok") is not True:
        raise RepositoryBootstrapError(f"{operation} returned an invalid bridge envelope")
    result = envelope.get("result")
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("error_code") if isinstance(result, dict) else "invalid_result"
        raise RepositoryBootstrapError(f"{operation} was rejected by the bridge: {detail}")
    return result


async def install_repository_bootstrap(bridge_tool) -> ResponseMessage:
    """Generate, validate, network-stage, and install the one canonical repository addon."""

    request_id = str(uuid.uuid4())
    try:
        build = build_repo_addon(repo_base_url=REPO_BASE_URL)
        if build.get("status") != "ok":
            raise RepositoryBootstrapError(f"canonical repository build failed: {build.get('error')}")
        artifact = validate_repository_bootstrap_zip(str(build.get("output_zip") or ""))

        before = await bridge_tool.get_bridge_addon_info(REPOSITORY_ADDON_ID)
        if before.error:
            raise RepositoryBootstrapError(f"pre-install addon inspection failed: {before.error}")

        stage = await bridge_tool.stage_repository_bootstrap(
            zip_path=str(artifact["path"]),
            version=str(artifact["version"]),
            sha256=str(artifact["sha256"]),
        )
        staged = _business_result(stage, "canonical repository staging")
        staged_zip = staged.get("repo_zip")
        if not isinstance(staged_zip, dict) or staged_zip.get("sha256") != artifact["sha256"]:
            raise RepositoryBootstrapError("bridge staged SHA-256 does not match canonical artifact")

        install = await bridge_tool.install_repository_bootstrap()
        installed = _business_result(install, "canonical repository installation")
        if installed.get("addon_id") != REPOSITORY_ADDON_ID:
            raise RepositoryBootstrapError("bridge installed unexpected addon id")
        if installed.get("version") != REPOSITORY_ADDON_VERSION:
            raise RepositoryBootstrapError("bridge installed unexpected repository version")
        if installed.get("artifact_sha256") != artifact["sha256"]:
            raise RepositoryBootstrapError("bridge installed artifact SHA-256 mismatch")

        after = await bridge_tool.get_bridge_addon_info(REPOSITORY_ADDON_ID)
        state = after.result if isinstance(after.result, dict) else {}
        if after.error or not state.get("installed") or not state.get("enabled"):
            raise RepositoryBootstrapError("post-install repository state is not installed and enabled")
        if state.get("version") != REPOSITORY_ADDON_VERSION:
            raise RepositoryBootstrapError("post-install repository version mismatch")

        return ResponseMessage(
            request_id=request_id,
            result={
                "ok": True,
                "action": installed.get("action"),
                "addon_id": REPOSITORY_ADDON_ID,
                "version": REPOSITORY_ADDON_VERSION,
                "enabled": True,
                "artifact_sha256": artifact["sha256"],
                "artifact_size_bytes": artifact["size_bytes"],
                "repository_base_url": REPO_BASE_URL,
            },
            error=None,
        )
    except RepositoryBootstrapError as exc:
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error=str(exc),
            error_type=ErrorType.INVALID_OPERATION,
        )
