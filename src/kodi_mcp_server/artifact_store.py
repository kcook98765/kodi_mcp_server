"""Minimal server-owned Artifact Store.

Purpose
-------
Provide an agent-safe indirection layer so remote clients can reference an
uploaded/built artifact by opaque id instead of a server-host filesystem path.

Scope (intentional)
-------------------
- Store zip files under a controlled directory.
- Maintain a tiny JSON index mapping artifact_id -> metadata.
- No lifecycle management, cleanup, auth, or multi-tenant concerns (yet).
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    path: str  # absolute path on server host (internal)
    addon_id: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "path": self.path,
            "addon_id": self.addon_id,
            "version": self.version,
        }


class ArtifactStore:
    """File-backed minimal artifact store.

    Index format:
        {
          "schema_version": 1,
          "artifacts": {
            "<artifact_id>": {"path": "...", "addon_id": "...", "version": "..."}
          }
        }
    """

    SCHEMA_VERSION = 1

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root_dir / "index.json"

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "artifacts": {}}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", self.SCHEMA_VERSION)
        data.setdefault("artifacts", {})
        if not isinstance(data.get("artifacts"), dict):
            data["artifacts"] = {}
        return data

    def _save_index(self, index: dict[str, Any]) -> None:
        self.index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")

    def register_existing_file(
        self,
        *,
        file_path: str | Path,
        addon_id: str | None = None,
        version: str | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        """Register an existing file path and return its artifact record.

        The file is copied into the store-controlled directory to ensure that
        the artifact is stable and does not depend on external paths.
        """

        p = Path(file_path).expanduser().resolve()
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"artifact file not found: {p}")

        artifact_id = artifact_id or str(uuid.uuid4())

        # Copy into store-controlled location.
        dest_name = f"{artifact_id}{p.suffix or '.zip'}"
        dest_path = (self.root_dir / dest_name).resolve()
        shutil.copy2(p, dest_path)
        index = self._load_index()
        artifacts = index.get("artifacts") or {}
        artifacts[str(artifact_id)] = {
            "path": str(dest_path),
            "addon_id": addon_id,
            "version": version,
        }
        index["artifacts"] = artifacts
        self._save_index(index)

        return ArtifactRecord(
            artifact_id=str(artifact_id),
            path=str(dest_path),
            addon_id=addon_id,
            version=version,
        )

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        artifact_id = str(artifact_id or "").strip()
        if not artifact_id:
            return None
        index = self._load_index()
        artifacts = index.get("artifacts") or {}
        raw = artifacts.get(artifact_id)
        if not isinstance(raw, dict):
            return None
        path = raw.get("path")
        if not isinstance(path, str) or not path:
            return None
        return ArtifactRecord(
            artifact_id=artifact_id,
            path=path,
            addon_id=raw.get("addon_id") if isinstance(raw.get("addon_id"), str) else None,
            version=raw.get("version") if isinstance(raw.get("version"), str) else None,
        )
