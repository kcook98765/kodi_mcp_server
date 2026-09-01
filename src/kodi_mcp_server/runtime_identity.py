"""Deterministic package and source identity for the running MCP server."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any

from kodi_mcp_server import __version__
from kodi_mcp_server.paths import PROJECT_ROOT

_BUILD_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SOURCE_ROOTS = (
    PROJECT_ROOT / "src" / "kodi_mcp_server",
    PROJECT_ROOT / "src" / "kodi_mcp_mcp",
)


def _package_version() -> str:
    try:
        declared = distribution_version("kodi_mcp_server")
    except PackageNotFoundError:
        declared = __version__
    if declared == "0.0.0" and __version__ != "0.0.0":
        return __version__
    return declared


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in _SOURCE_ROOTS:
        if not root.is_dir():
            continue
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        )
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)
    return sorted(set(files), key=lambda path: path.relative_to(PROJECT_ROOT).as_posix())


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in _source_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def capture_runtime_identity() -> dict[str, Any]:
    package_version = _package_version()
    environment_sha = str(os.environ.get("KODI_MCP_BUILD_SHA") or "").strip()
    if environment_sha and not _BUILD_SHA_RE.fullmatch(environment_sha):
        raise ValueError("KODI_MCP_BUILD_SHA must contain 7-64 hexadecimal characters")

    git_sha = environment_sha.lower() if environment_sha else _git("rev-parse", "HEAD")
    fingerprint = source_fingerprint()
    status = _git("status", "--porcelain", "--", "src", "pyproject.toml")
    dirty = bool(status) if status is not None else None
    sha_component = f"g{git_sha[:12]}" if git_sha else "package"

    return {
        "name": "kodi-mcp",
        "version": package_version,
        "git_sha": git_sha,
        "source_dirty": dirty,
        "source_root": str(PROJECT_ROOT),
        "source_fingerprint_sha256": fingerprint,
        "build_id": f"{package_version}+{sha_component}.{fingerprint[:12]}",
        "provenance": "build_environment" if environment_sha else ("git_checkout" if git_sha else "package_metadata"),
    }


STARTUP_RUNTIME_IDENTITY = capture_runtime_identity()
