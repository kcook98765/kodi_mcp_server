"""Pure repository mutation helpers for kodi_mcp_server.

Artifact flow:
- source addon packages: `kodi_addon/packages/...`
- built compatibility zip artifacts: `addon/*.zip`
- authoritative published repo content: `repo/dev-repo/...`
"""

import hashlib
import re
import shutil
from pathlib import Path

from .artifacts import AddonArtifact


class RepoPublisher:
    """Low-level repository mutation/build operations.

    This layer consumes structured artifact metadata rather than relying on
    implicit path conventions spread across scripts.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.dev_repo = self.repo_root / "dev-repo"
        self.zips_dir = self.dev_repo / "zips"

    def publish_addon_artifact(self, artifact: AddonArtifact) -> dict:
        """Publish an addon artifact into the dev repo and update repo metadata."""
        addon_zip_path = artifact.legacy_build_zip_path
        if not addon_zip_path.exists():
            raise FileNotFoundError(f"Addon zip file not found: {addon_zip_path}")

        self.zips_dir.mkdir(parents=True, exist_ok=True)
        artifact.repo_addon_zips_root.mkdir(parents=True, exist_ok=True)

        repo_zip_path = artifact.repo_zip_path
        shutil.copy2(addon_zip_path, repo_zip_path)

        addons_xml_path = self.dev_repo / "addons.xml"
        if addons_xml_path.exists():
            addons_xml_content = addons_xml_path.read_text(encoding="utf-8")
        else:
            addons_xml_content = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<addons>\n'
                '</addons>\n'
            )

        new_addon_entry = self.create_addon_entry(
            addon_id=artifact.addon_id,
            addon_name=artifact.addon_name,
            addon_version=artifact.addon_version,
            provider_name=artifact.provider_name,
        )

        if f'id="{artifact.addon_id}"' in addons_xml_content:
            pattern = rf'<addon id="{re.escape(artifact.addon_id)}"[^>]*>.*?</addon>'
            addons_xml_content = re.sub(
                pattern,
                new_addon_entry,
                addons_xml_content,
                flags=re.DOTALL,
            )
            action = "updated"
        else:
            addons_xml_content = addons_xml_content.replace(
                '</addons>',
                f'\n{new_addon_entry}\n</addons>'
            )
            action = "added"

        addons_xml_path.write_text(addons_xml_content, encoding="utf-8")

        md5_checksum = hashlib.md5(addons_xml_path.read_bytes()).hexdigest()
        (self.dev_repo / "addons.xml.md5").write_text(
            f"{md5_checksum}  addons.xml\n",
            encoding="utf-8",
        )

        return {
            "status": "success",
            "action": action,
            "addon_id": artifact.addon_id,
            "addon_name": artifact.addon_name,
            "addon_version": artifact.addon_version,
            "source_dir": str(artifact.source_dir),
            "build_zip_path": str(artifact.legacy_build_zip_path),
            "zip_path": str(repo_zip_path),
            "addons_xml_path": str(addons_xml_path),
            "md5_checksum": md5_checksum,
        }

    def publish_addon(
        self,
        addon_zip_path: str,
        addon_id: str,
        addon_name: str,
        addon_version: str,
        provider_name: str = "kodi_mcp",
    ) -> dict:
        """Compatibility entrypoint that adapts legacy publish inputs into an artifact model."""
        artifact = AddonArtifact(
            addon_id=addon_id,
            addon_name=addon_name,
            addon_version=addon_version,
            provider_name=provider_name,
            repo_root=self.repo_root,
            build_root=Path(addon_zip_path).resolve().parent,
        )
        return self.publish_addon_artifact(artifact)

    @staticmethod
    def create_addon_entry(
        addon_id: str,
        addon_name: str,
        addon_version: str,
        provider_name: str,
    ) -> str:
        """Create an addon XML entry."""
        return f'''<addon id="{addon_id}" name="{addon_name}" version="{addon_version}" provider-name="{provider_name}">
    <requires>
        <import addon="xbmc.python" version="3.0.0"/>
    </requires>
    <extension point="xbmc.python.script" library="default.py"/>
    <extension point="xbmc.addon.metadata">
        <summary>{addon_name}</summary>
        <description>Kodi MCP test addon</description>
        <platform>all</platform>
        <license>MIT</license>
    </extension>
</addon>'''
