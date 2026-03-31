"""Shared addon artifact model for kodi_mcp_server.

Artifact lifecycle ownership:
- source package content lives under `kodi_addon/packages/<addon_id>/`
- compatibility build output is written to `addon/<addon_id>-<version>.zip`
- authoritative published repo copy is written under `repo/dev-repo/...`

External behavior is intentionally unchanged; this module only centralizes the
internal representation of those related paths.
"""

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import re

from .paths import (
    AUTHORITATIVE_REPO_ROOT,
    KODI_ADDON_PACKAGES_ROOT,
    LEGACY_ADDON_ARTIFACTS_ROOT,
)


@dataclass(frozen=True)
class AddonArtifact:
    """Canonical internal representation of an addon artifact across source/build/publish."""

    addon_id: str
    addon_name: str
    addon_version: str
    provider_name: str = "kodi_mcp"
    package_root: Path = KODI_ADDON_PACKAGES_ROOT
    build_root: Path = LEGACY_ADDON_ARTIFACTS_ROOT
    repo_root: Path = AUTHORITATIVE_REPO_ROOT

    @property
    def source_dir(self) -> Path:
        return self.package_root / self.addon_id

    @property
    def addon_xml_path(self) -> Path:
        return self.source_dir / "addon.xml"

    @property
    def legacy_build_zip_name(self) -> str:
        return f"{self.addon_id}-{self.addon_version}.zip"

    @property
    def legacy_build_zip_path(self) -> Path:
        return self.build_root / self.legacy_build_zip_name

    @property
    def repo_dev_root(self) -> Path:
        return self.repo_root / "dev-repo"

    @property
    def repo_zips_root(self) -> Path:
        return self.repo_dev_root / "zips"

    @property
    def repo_addon_zips_root(self) -> Path:
        return self.repo_zips_root / self.addon_id

    @property
    def repo_zip_path(self) -> Path:
        return self.repo_addon_zips_root / self.legacy_build_zip_name

    def zip_members(self) -> list[tuple[Path, str]]:
        """Return `(src_path, archive_name)` tuples for zip creation."""
        members: list[tuple[Path, str]] = []
        for path in sorted(self.source_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                members.append((path, path.relative_to(self.package_root).as_posix()))
        return members

    def build_legacy_zip(self) -> Path:
        """Build the legacy compatibility zip artifact at `addon/*.zip`."""
        zip_path = self.legacy_build_zip_path
        if zip_path.exists():
            zip_path.unlink()
        with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
            for src_path, archive_name in self.zip_members():
                zf.write(src_path, archive_name)
        return zip_path


def read_addon_version(addon_xml_path: Path) -> str:
    """Read the addon version attribute from an addon.xml file."""
    text = addon_xml_path.read_text(encoding="utf-8")
    match = re.search(r'<addon\b[^>]*\bversion="([^"]+)"', text)
    if not match:
        raise ValueError(f"could not find addon version in {addon_xml_path}")
    return match.group(1)


def build_test_addon_artifact(version: str) -> AddonArtifact:
    """Return the canonical artifact model for script.kodi_mcp_test."""
    return AddonArtifact(
        addon_id="script.kodi_mcp_test",
        addon_name="Kodi MCP Test Script",
        addon_version=version,
        provider_name="kodi_mcp",
    )


def build_service_addon_artifact(version: str) -> AddonArtifact:
    """Return the canonical artifact model for service.kodi_mcp."""
    return AddonArtifact(
        addon_id="service.kodi_mcp",
        addon_name="Kodi MCP Service",
        addon_version=version,
        provider_name="kodi_mcp",
    )
