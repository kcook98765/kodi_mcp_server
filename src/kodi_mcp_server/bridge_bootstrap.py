"""Read-only, user-assisted first-install contract for service.kodi_mcp."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

BRIDGE_ADDON_ID = "service.kodi_mcp"
BOOTSTRAP_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ZipResourcePolicy:
    max_artifact_bytes: int
    max_members: int
    max_member_uncompressed_bytes: int
    max_total_uncompressed_bytes: int
    max_expansion_ratio: float
    max_required_member_bytes: int
    allowed_compression_methods: frozenset[int]
    encrypted_members_forbidden: bool


BOOTSTRAP_ZIP_POLICY = ZipResourcePolicy(
    max_artifact_bytes=8 * 1024 * 1024,
    max_members=256,
    max_member_uncompressed_bytes=2 * 1024 * 1024,
    max_total_uncompressed_bytes=4 * 1024 * 1024,
    max_expansion_ratio=100.0,
    max_required_member_bytes=64 * 1024,
    allowed_compression_methods=frozenset(
        {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    ),
    encrypted_members_forbidden=True,
)
MAX_BOOTSTRAP_ARTIFACT_BYTES = BOOTSTRAP_ZIP_POLICY.max_artifact_bytes


class BootstrapBundleError(RuntimeError):
    """The configured first-install bundle is absent, unsafe, or inconsistent."""


@dataclass(frozen=True)
class BootstrapBundle:
    addon_id: str
    version: str
    artifact_path: Path
    artifact_bytes: bytes
    artifact_sha256: str
    source_git_sha: str
    source_fingerprint_sha256: str

    def public_manifest(self, base_url: str) -> dict[str, Any]:
        return {
            "schema_version": BOOTSTRAP_SCHEMA_VERSION,
            "addon_id": self.addon_id,
            "version": self.version,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size_bytes": len(self.artifact_bytes),
            "source_git_sha": self.source_git_sha,
            "source_fingerprint_sha256": self.source_fingerprint_sha256,
            "download_url": urljoin(base_url.rstrip("/") + "/", "bootstrap/service.kodi_mcp.zip"),
            "manifest_url": urljoin(base_url.rstrip("/") + "/", "bootstrap/manifest.json"),
        }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BootstrapBundleError(f"bootstrap manifest field {key!r} must be a non-empty string")
    return value.strip()


def _read_bounded_artifact(path: Path) -> bytes:
    with path.open("rb") as handle:
        artifact_bytes = handle.read(MAX_BOOTSTRAP_ARTIFACT_BYTES + 1)
    if len(artifact_bytes) > MAX_BOOTSTRAP_ARTIFACT_BYTES:
        raise BootstrapBundleError(
            "bootstrap artifact exceeds maximum size of "
            f"{MAX_BOOTSTRAP_ARTIFACT_BYTES} bytes"
        )
    return artifact_bytes


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(_read_bounded_artifact(path))


def write_bootstrap_manifest(build: dict[str, Any], output_path: Path | str) -> Path:
    """Write the stable sidecar consumed by the read-only bootstrap surface."""

    output = Path(output_path).expanduser()
    artifact = Path(_required_string(build, "artifact")).expanduser().resolve()
    if not artifact.is_file():
        raise BootstrapBundleError(f"bootstrap artifact is missing: {artifact}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if artifact.parent != output.parent.resolve():
        raise BootstrapBundleError(
            "bootstrap manifest must be written in the same directory as the artifact"
        )

    addon_id = _required_string(build, "addon_id")
    version = _required_string(build, "version")
    artifact_sha256 = _required_string(build, "artifact_sha256").lower()
    source_git_sha = _required_string(build, "head").lower()
    source_fingerprint = _required_string(build, "source_fingerprint_sha256").lower()
    actual_sha256 = _sha256(artifact)
    if not hmac.compare_digest(actual_sha256, artifact_sha256):
        raise BootstrapBundleError(
            f"canonical build SHA-256 mismatch: got {actual_sha256}, expected {artifact_sha256}"
        )

    payload = {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "addon_id": addon_id,
        "version": version,
        "artifact": artifact.name,
        "artifact_sha256": artifact_sha256,
        "source_git_sha": source_git_sha,
        "source_fingerprint_sha256": source_fingerprint,
    }
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        load_bootstrap_bundle(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _validate_zip_metadata(
    archive: zipfile.ZipFile,
    required_names: tuple[str, ...],
    policy: ZipResourcePolicy = BOOTSTRAP_ZIP_POLICY,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > policy.max_members:
        raise BootstrapBundleError(
            f"bootstrap artifact member count exceeds maximum of {policy.max_members}"
        )

    seen_names: set[str] = set()
    total_uncompressed = 0
    total_compressed = 0
    by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.compress_type not in policy.allowed_compression_methods:
            raise BootstrapBundleError(
                "bootstrap artifact contains an unsupported compression method"
            )
        if policy.encrypted_members_forbidden and (info.flag_bits & 0x1):
            raise BootstrapBundleError(
                "bootstrap artifact encrypted members are forbidden"
            )

        name = info.filename
        folded_name = name.casefold()
        if folded_name in seen_names:
            raise BootstrapBundleError(
                "bootstrap artifact contains a duplicate or case-ambiguous member name"
            )
        seen_names.add(folded_name)

        member_path = PurePosixPath(name)
        if (
            not name
            or name.startswith(("/", "\\"))
            or "\\" in name
            or bool(PureWindowsPath(name).drive)
            or ".." in member_path.parts
        ):
            raise BootstrapBundleError("bootstrap artifact contains an unsafe member path")
        if info.file_size < 0 or info.compress_size < 0:
            raise BootstrapBundleError("bootstrap artifact contains impossible member sizes")
        if info.file_size > policy.max_member_uncompressed_bytes:
            raise BootstrapBundleError(
                "bootstrap artifact member uncompressed size exceeds maximum of "
                f"{policy.max_member_uncompressed_bytes} bytes"
            )

        total_uncompressed += info.file_size
        if total_uncompressed > policy.max_total_uncompressed_bytes:
            raise BootstrapBundleError(
                "bootstrap artifact aggregate uncompressed size exceeds maximum of "
                f"{policy.max_total_uncompressed_bytes} bytes"
            )
        total_compressed += info.compress_size
        if total_compressed > policy.max_artifact_bytes:
            raise BootstrapBundleError(
                "bootstrap artifact aggregate compressed size exceeds maximum of "
                f"{policy.max_artifact_bytes} bytes"
            )

        if info.file_size:
            if info.compress_size == 0:
                raise BootstrapBundleError(
                    "bootstrap artifact member expansion ratio is unbounded"
                )
            if info.file_size / info.compress_size > policy.max_expansion_ratio:
                raise BootstrapBundleError(
                    "bootstrap artifact member expansion ratio exceeds maximum of "
                    f"{policy.max_expansion_ratio:g}"
                )
        by_name[name] = info

    if total_uncompressed:
        if total_compressed == 0:
            raise BootstrapBundleError(
                "bootstrap artifact aggregate expansion ratio is unbounded"
            )
        if total_uncompressed / total_compressed > policy.max_expansion_ratio:
            raise BootstrapBundleError(
                "bootstrap artifact aggregate expansion ratio exceeds maximum of "
                f"{policy.max_expansion_ratio:g}"
            )

    missing = [name for name in required_names if name not in by_name]
    if missing:
        raise BootstrapBundleError(
            "bootstrap artifact is missing required member(s): " + ", ".join(missing)
        )
    required = {name: by_name[name] for name in required_names}
    if any(info.file_size > policy.max_required_member_bytes for info in required.values()):
        raise BootstrapBundleError(
            "bootstrap artifact required member exceeds maximum size of "
            f"{policy.max_required_member_bytes} bytes"
        )
    return required


def _read_required_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    policy: ZipResourcePolicy = BOOTSTRAP_ZIP_POLICY,
) -> bytes:
    if info.file_size > policy.max_required_member_bytes:
        raise BootstrapBundleError(
            "bootstrap artifact required member exceeds maximum size of "
            f"{policy.max_required_member_bytes} bytes"
        )
    with archive.open(info, "r") as handle:
        data = handle.read(policy.max_required_member_bytes + 1)
    if len(data) > policy.max_required_member_bytes or len(data) != info.file_size:
        raise BootstrapBundleError("bootstrap artifact required member read exceeded its bound")
    return data


def load_bootstrap_bundle(manifest_path: Path | str) -> BootstrapBundle:
    """Validate the pinned bridge artifact before it can be advertised or served."""

    path = Path(manifest_path).expanduser()
    if not path.is_file():
        raise BootstrapBundleError(f"bootstrap manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapBundleError(f"bootstrap manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise BootstrapBundleError("bootstrap manifest must be a JSON object")
    if payload.get("schema_version") != BOOTSTRAP_SCHEMA_VERSION:
        raise BootstrapBundleError(
            f"bootstrap manifest schema_version must be {BOOTSTRAP_SCHEMA_VERSION}"
        )

    addon_id = _required_string(payload, "addon_id")
    if addon_id != BRIDGE_ADDON_ID:
        raise BootstrapBundleError(
            f"bootstrap manifest addon id is {addon_id!r}, expected {BRIDGE_ADDON_ID!r}"
        )
    version = _required_string(payload, "version")
    artifact_name = _required_string(payload, "artifact")
    artifact_sha256 = _required_string(payload, "artifact_sha256").lower()
    source_git_sha = _required_string(payload, "source_git_sha").lower()
    source_fingerprint = _required_string(payload, "source_fingerprint_sha256").lower()

    if not _SHA256_RE.fullmatch(artifact_sha256):
        raise BootstrapBundleError("artifact SHA-256 must be 64 lowercase hexadecimal characters")
    if not _SHA256_RE.fullmatch(source_fingerprint):
        raise BootstrapBundleError("source fingerprint must be 64 lowercase hexadecimal characters")
    if not _GIT_SHA_RE.fullmatch(source_git_sha):
        raise BootstrapBundleError("source Git SHA must be 40 lowercase hexadecimal characters")

    relative_artifact = Path(artifact_name)
    if relative_artifact.is_absolute() or relative_artifact.name != artifact_name:
        raise BootstrapBundleError("bootstrap artifact must be a filename in the same directory as its manifest")
    manifest_dir = path.resolve().parent
    artifact_candidate = manifest_dir / relative_artifact
    if not artifact_candidate.is_file():
        raise BootstrapBundleError(f"bootstrap artifact is missing: {artifact_candidate}")
    artifact_path = artifact_candidate.resolve()
    if artifact_path.parent != manifest_dir:
        raise BootstrapBundleError(
            "bootstrap artifact must resolve to a file in the same directory as its manifest"
        )
    artifact_bytes = _read_bounded_artifact(artifact_path)
    actual_sha256 = _sha256_bytes(artifact_bytes)
    if not hmac.compare_digest(actual_sha256, artifact_sha256):
        raise BootstrapBundleError(
            f"bootstrap artifact SHA-256 mismatch: got {actual_sha256}, expected {artifact_sha256}"
        )

    try:
        with zipfile.ZipFile(io.BytesIO(artifact_bytes), "r") as archive:
            addon_xml_name = f"{BRIDGE_ADDON_ID}/addon.xml"
            build_manifest_name = f"{BRIDGE_ADDON_ID}/build_manifest.json"
            required = _validate_zip_metadata(
                archive,
                (addon_xml_name, build_manifest_name),
            )
            # Full CRC validation is safe only after metadata proves the entire
            # uncompressed archive fits the explicit resource policy.
            bad_member = archive.testzip()
            if bad_member is not None:
                raise BootstrapBundleError(
                    f"bootstrap artifact has a corrupt ZIP member: {bad_member}"
                )
            addon_xml_bytes = _read_required_member(archive, required[addon_xml_name])
            build_manifest_bytes = _read_required_member(
                archive,
                required[build_manifest_name],
            )
            addon_xml = ElementTree.fromstring(addon_xml_bytes)
            build_manifest = json.loads(build_manifest_bytes.decode("utf-8"))
    except BootstrapBundleError:
        raise
    except (
        OSError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise BootstrapBundleError(f"bootstrap artifact is invalid: {exc}") from exc

    packaged_addon_id = addon_xml.get("id")
    if packaged_addon_id != addon_id:
        raise BootstrapBundleError(
            f"bootstrap artifact addon id is {packaged_addon_id!r}, expected {addon_id!r}"
        )
    packaged_version = addon_xml.get("version")
    if packaged_version != version:
        raise BootstrapBundleError(
            f"bootstrap artifact version is {packaged_version!r}, expected {version!r}"
        )
    if not isinstance(build_manifest, dict):
        raise BootstrapBundleError("bootstrap artifact build manifest must be a JSON object")
    packaged_git_sha = str(build_manifest.get("source_git_sha") or "").lower()
    if packaged_git_sha != source_git_sha:
        raise BootstrapBundleError(
            f"bootstrap artifact source Git SHA is {packaged_git_sha!r}, expected {source_git_sha!r}"
        )
    packaged_fingerprint = str(build_manifest.get("source_fingerprint_sha256") or "").lower()
    if packaged_fingerprint != source_fingerprint:
        raise BootstrapBundleError(
            "bootstrap artifact source fingerprint is "
            f"{packaged_fingerprint!r}, expected {source_fingerprint!r}"
        )

    return BootstrapBundle(
        addon_id=addon_id,
        version=version,
        artifact_path=artifact_path,
        artifact_bytes=artifact_bytes,
        artifact_sha256=artifact_sha256,
        source_git_sha=source_git_sha,
        source_fingerprint_sha256=source_fingerprint,
    )


def _unsupported(reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "bootstrap_unsupported",
        "installed": None,
        "verified": False,
        "reason": reason,
        "next_stage": None,
        "mutated": False,
    }


def _user_action(
    *,
    bundle: BootstrapBundle,
    base_url: str,
    installed: bool,
    reason: str,
    actions: list[str],
) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "user_action_required",
        "installed": installed,
        "verified": False,
        "reason": reason,
        "artifact": bundle.public_manifest(base_url),
        "user_actions": actions,
        "resume": {
            "tool": "bridge_bootstrap_status",
            "success_condition": (
                "state=already_installed, verified=true, and next_stage=managed_deployment"
            ),
        },
        "next_stage": None,
        "mutated": False,
    }


def _validate_bridge_health(payload: Any) -> tuple[bool, str]:
    """Validate the canonical service.kodi_mcp health response."""

    if not isinstance(payload, dict):
        return False, "result must be an object"
    if payload.get("status") != "ok":
        return False, "status must be 'ok'"
    if payload.get("service") != BRIDGE_ADDON_ID:
        return False, f"service must be {BRIDGE_ADDON_ID!r}"
    if payload.get("addon_id") != BRIDGE_ADDON_ID:
        return False, f"addon_id must be {BRIDGE_ADDON_ID!r}"
    health_type = payload.get("health_type")
    if health_type not in {"shallow", "deep"}:
        return False, "health_type must be 'shallow' or 'deep'"
    for flag in ("ok", "healthy", "ready"):
        if flag in payload and payload.get(flag) is not True:
            return False, f"{flag} must be true when present"
    if health_type == "deep" and payload.get("kodi_jsonrpc_ok") is not True:
        return False, "deep health requires kodi_jsonrpc_ok=true"
    return True, "affirmative canonical bridge health"


async def inspect_bootstrap_state(
    *,
    jsonrpc_tool: Any,
    bridge_tool: Any,
    manifest_path: Path | str,
    base_url: str,
) -> dict[str, Any]:
    """Classify first-install state without changing Kodi or server files."""

    try:
        bundle = load_bootstrap_bundle(manifest_path)
    except BootstrapBundleError as exc:
        return _unsupported(str(exc))

    version_response = await jsonrpc_tool.get_jsonrpc_version()
    if getattr(version_response, "error", None):
        return _unsupported(
            f"Kodi JSON-RPC is unavailable: {getattr(version_response, 'error', None)}"
        )

    details_response = await jsonrpc_tool.get_addon_details(bundle.addon_id)
    details_error = getattr(details_response, "error", None)
    details_error_code = getattr(details_response, "error_code", None)
    details_result = getattr(details_response, "result", None)
    addon = details_result.get("addon") if isinstance(details_result, dict) else None
    addon_absent = bool(
        details_error
        and (
            details_error_code == -32602
            or "-32602" in str(details_error)
        )
    )
    if details_error and not addon_absent:
        return _unsupported(
            "Kodi addon state could not be determined: " + str(details_error)
        )
    if not addon_absent and not isinstance(addon, dict):
        return _unsupported("Kodi addon state response did not contain addon metadata")
    if addon_absent:
        return _user_action(
            bundle=bundle,
            base_url=base_url,
            installed=False,
            reason=(
                "Stock Kodi exposes no JSON-RPC operation that can install an arbitrary ZIP "
                "or add a repository/source. One explicit Kodi UI installation is required."
            ),
            actions=[
                "Download or expose the exact bridge ZIP at the returned download_url to Kodi.",
                "In Kodi, review the Unknown Sources warning; Kodi MCP will not change that setting.",
                "Open Add-ons > Install from zip file and select the exact service.kodi_mcp ZIP.",
                "Configure Kodi MCP Service with the same shared token as this MCP server.",
                "Run bridge_bootstrap_status again.",
            ],
        )

    assert isinstance(addon, dict)
    if addon.get("enabled") is not True:
        return _user_action(
            bundle=bundle,
            base_url=base_url,
            installed=True,
            reason="Kodi reports the bridge installed but disabled.",
            actions=[
                "Enable Kodi MCP Service in Kodi Add-ons.",
                "Configure its shared token to match this MCP server.",
                "Run bridge_bootstrap_status again.",
            ],
        )

    health_response = await bridge_tool.get_bridge_health()
    if getattr(health_response, "error", None):
        return _user_action(
            bundle=bundle,
            base_url=base_url,
            installed=True,
            reason=(
                "Kodi reports the bridge installed and enabled, but its authenticated HTTP service "
                f"is unavailable: {getattr(health_response, 'error', None)}"
            ),
            actions=[
                "Open Kodi MCP Service settings and configure the shared token to match this MCP server.",
                "If the token is already correct, restart Kodi or disable and re-enable the service.",
                "Run bridge_bootstrap_status again.",
            ],
        )

    health_payload = getattr(health_response, "result", None)
    health_ok, health_reason = _validate_bridge_health(health_payload)
    if not health_ok:
        return _user_action(
            bundle=bundle,
            base_url=base_url,
            installed=True,
            reason=f"Bridge health payload is not affirmative: {health_reason}.",
            actions=[
                "Verify Kodi MCP Service is running and reports canonical healthy status.",
                "Restart Kodi or disable and re-enable the service if needed.",
                "Run bridge_bootstrap_status again.",
            ],
        )

    status_response = await bridge_tool.get_bridge_status()
    if getattr(status_response, "error", None):
        return _user_action(
            bundle=bundle,
            base_url=base_url,
            installed=True,
            reason=f"Bridge health passed but identity status failed: {status_response.error}",
            actions=["Retry bridge_bootstrap_status; if it persists, inspect bridge health/logs."],
        )

    status_payload = getattr(status_response, "result", None)
    status: dict[str, Any] = status_payload if isinstance(status_payload, dict) else {}
    build_payload = status.get("build")
    build: dict[str, Any] = build_payload if isinstance(build_payload, dict) else {}
    observed = {
        "addon_id": status.get("addon_id") or status.get("id"),
        "kodi_addon_version": addon.get("version"),
        "bridge_version": status.get("addon_version") or status.get("version"),
        "source_git_sha": build.get("source_git_sha"),
        "source_fingerprint_sha256": build.get("source_fingerprint_sha256"),
    }
    expected = {
        "addon_id": bundle.addon_id,
        "kodi_addon_version": bundle.version,
        "bridge_version": bundle.version,
        "source_git_sha": bundle.source_git_sha,
        "source_fingerprint_sha256": bundle.source_fingerprint_sha256,
    }
    mismatches = [key for key, value in expected.items() if observed.get(key) != value]
    verified = not mismatches
    return {
        "ok": True,
        "state": "already_installed",
        "installed": True,
        "verified": verified,
        "artifact": bundle.public_manifest(base_url),
        "expected_identity": expected,
        "observed_identity": observed,
        "identity_mismatches": mismatches,
        "next_stage": "managed_deployment" if verified else "authoritative_update",
        "reason": (
            "Exact authoritative bridge identity verified."
            if verified
            else "Installed bridge identity is not the configured authoritative build."
        ),
        "mutated": False,
    }
