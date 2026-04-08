"""Helpers for parsing Kodi addon.xml files.

Milestone B (part 1) uses this to validate/identify local addon roots.

Parsing is intentionally small and robust:
- For addon id we parse the first `<addon ...>` tag via regex.
- For addon version we reuse the existing `artifacts.read_addon_version()` helper.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from .artifacts import read_addon_version as _read_addon_version


_ADDON_TAG_RE = re.compile(r"<addon\b[^>]*>")
_ATTR_ID_RE = re.compile(r"\bid=\"([^\"]+)\"")


def _read_addon_tag_text(addon_xml_path: Path) -> str:
    text = addon_xml_path.read_text(encoding="utf-8", errors="replace")
    match = _ADDON_TAG_RE.search(text)
    if not match:
        raise ValueError(f"could not find <addon ...> tag in {addon_xml_path}")
    return match.group(0)


def read_addon_id(addon_xml_path: Path) -> str:
    """Read the addon id attribute from addon.xml."""

    tag = _read_addon_tag_text(addon_xml_path)
    match = _ATTR_ID_RE.search(tag)
    if not match:
        raise ValueError(f"could not find addon id in {addon_xml_path}")
    return match.group(1)


def read_addon_version(addon_xml_path: Path) -> str:
    """Read the addon version attribute from addon.xml.

    This is a thin wrapper around the existing implementation in
    `kodi_mcp_server.artifacts` so version parsing behavior stays consistent.
    """

    return _read_addon_version(addon_xml_path)


def read_addon_id_and_version(addon_xml_path: Path) -> Tuple[str, str]:
    """Read (addon_id, version) from addon.xml."""

    return read_addon_id(addon_xml_path), read_addon_version(addon_xml_path)
