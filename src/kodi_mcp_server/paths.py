"""Authoritative project path helpers for kodi_mcp_server.

These helpers make ownership explicit:
- source addon packages live under `kodi_addon/packages/`
- legacy compatibility build artifacts live under `addon/`
- authoritative served/published repo content lives under project-root `repo/`
- `server/repo*` paths are legacy and must not be used for runtime serving or publishing
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KODI_ADDON_PACKAGES_ROOT = PROJECT_ROOT / "kodi_addon" / "packages"
LEGACY_ADDON_ARTIFACTS_ROOT = PROJECT_ROOT / "addon"
AUTHORITATIVE_REPO_ROOT = PROJECT_ROOT / "repo"
LEGACY_SERVER_REPO_ROOT = PROJECT_ROOT / "server" / "repo"
LEGACY_SERVER_REPO_LEGACY_ROOT = PROJECT_ROOT / "server" / "repo_legacy"


def assert_not_legacy_repo_path(path: Path) -> Path:
    """Guard against accidental use of the legacy server repo tree."""
    resolved = path.resolve()
    legacy_candidates = [LEGACY_SERVER_REPO_ROOT, LEGACY_SERVER_REPO_LEGACY_ROOT]
    for legacy in legacy_candidates:
        try:
            if resolved == legacy.resolve() or legacy.resolve() in resolved.parents:
                raise ValueError(
                    f"legacy server repo path is not authoritative and must not be used: {resolved}"
                )
        except FileNotFoundError:
            # resolve() can fail for missing paths on some platforms; compare lexically as fallback
            if str(resolved).startswith(str(legacy)):
                raise ValueError(
                    f"legacy server repo path is not authoritative and must not be used: {resolved}"
                )
    return path
