import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from kodi_mcp_server.repo_ops import RepoPublisher


def test_publish_preserves_authoritative_addon_manifest(tmp_path: Path):
    addon_id = "plugin.video.kodi_mcp_test_lab"
    addon_xml = """<?xml version="1.0" encoding="UTF-8"?>
<addon id="plugin.video.kodi_mcp_test_lab" name="Kodi MCP Test Lab" version="0.1.0" provider-name="Nous Research">
  <requires><import addon="xbmc.python" version="3.0.0"/></requires>
  <extension point="xbmc.python.pluginsource" library="main.py"><provides>video</provides></extension>
  <extension point="xbmc.addon.metadata"><summary>Kodi MCP Test Lab</summary><description>Diagnostics</description><platform>all</platform><license>MIT</license></extension>
</addon>
"""
    addon_zip = tmp_path / f"{addon_id}-0.1.0.zip"
    with zipfile.ZipFile(addon_zip, "w") as archive:
        archive.writestr(f"{addon_id}/addon.xml", addon_xml)
        archive.writestr(f"{addon_id}/main.py", "pass\n")

    RepoPublisher(tmp_path / "repo").publish_addon(
        addon_zip_path=str(addon_zip),
        addon_id=addon_id,
        addon_name="Kodi MCP Test Lab",
        addon_version="0.1.0",
        provider_name="Nous Research",
    )

    published = ET.parse(tmp_path / "repo" / "dev-repo" / "addons.xml").getroot().find(
        f"./addon[@id='{addon_id}']"
    )
    assert published is not None
    plugin = published.find("./extension[@point='xbmc.python.pluginsource']")
    assert plugin is not None
    assert plugin.get("library") == "main.py"
    assert plugin.findtext("provides") == "video"
