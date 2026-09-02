#!/usr/bin/env python3
"""Prepare the authoritative bridge ZIP/manifest without contacting Kodi."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deploy_current_bridge import DEFAULT_SOURCE, prepare_build
from kodi_mcp_server.bridge_bootstrap import write_bootstrap_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and pin the service.kodi_mcp first-install bundle."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Output manifest; defaults beside the built ZIP as bridge-bootstrap.json.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    build = prepare_build(args.source)
    artifact = Path(build["artifact"])
    manifest_path = args.manifest or artifact.parent / "bridge-bootstrap.json"
    written = write_bootstrap_manifest(build, manifest_path)
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(written),
                "artifact": str(artifact),
                "addon_id": build["addon_id"],
                "version": build["version"],
                "artifact_sha256": build["artifact_sha256"],
                "source_git_sha": build["head"],
                "source_fingerprint_sha256": build["source_fingerprint_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
