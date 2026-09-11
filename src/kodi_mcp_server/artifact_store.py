"""Minimal server-owned Artifact Store.

Registration is serialized by the global workflow lock. New artifact bytes are
written to a unique same-directory temporary, fsynced, and atomically activated
before the atomic index update. A crash between activation and index publication
may leave a recoverable unindexed orphan, never a partial registered artifact.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from kodi_mcp_server.orchestration_locking import acquire_global_workflow_lock


class ArtifactConflictError(ValueError):
    error_code = "ARTIFACT_ID_CONFLICT"

    def __init__(self) -> None:
        super().__init__("artifact id is already registered")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _global_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with acquire_global_workflow_lock():
            return method(self, *args, **kwargs)

    return locked


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    path: str
    addon_id: str | None = None
    version: str | None = None
    addon_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "path": self.path,
            "addon_id": self.addon_id,
            "version": self.version,
            "addon_name": self.addon_name,
        }


class ArtifactStore:
    SCHEMA_VERSION = 1

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
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
        self.root_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".index-", dir=self.root_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                temporary.write(json.dumps(index, indent=2, sort_keys=True))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.index_path)
            _fsync_directory(self.root_dir)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _begin_registration(
        self, artifact_id: str | None, suffix: str
    ) -> tuple[str, dict[str, Any], Path, Path]:
        artifact_id = artifact_id or str(uuid.uuid4())
        index = self._load_index()
        artifacts = index.get("artifacts") or {}
        if str(artifact_id) in artifacts:
            raise ArtifactConflictError()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        final_path = (self.root_dir / f"{artifact_id}{suffix}").resolve()
        fd, temporary_name = tempfile.mkstemp(
            prefix=".artifact-", suffix=suffix, dir=self.root_dir
        )
        os.close(fd)
        return str(artifact_id), index, Path(temporary_name), final_path

    def _finish_registration(
        self,
        *,
        artifact_id: str,
        index: dict[str, Any],
        temporary: Path,
        final_path: Path,
        addon_id: str | None,
        version: str | None,
        addon_name: str | None,
    ) -> ArtifactRecord:
        os.replace(temporary, final_path)
        _fsync_directory(self.root_dir)
        artifacts = index.get("artifacts") or {}
        artifacts[artifact_id] = {
            "path": str(final_path),
            "addon_id": addon_id,
            "version": version,
            "addon_name": addon_name,
        }
        index["artifacts"] = artifacts
        self._save_index(index)
        return ArtifactRecord(
            artifact_id=artifact_id,
            path=str(final_path),
            addon_id=addon_id,
            version=version,
            addon_name=addon_name,
        )

    @staticmethod
    def _cleanup_temporary(temporary: Path) -> None:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    @_global_locked
    def register_existing_file(
        self,
        *,
        file_path: str | Path,
        addon_id: str | None = None,
        version: str | None = None,
        addon_name: str | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        source = Path(file_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"artifact file not found: {source}")
        suffix = source.suffix or ".zip"
        artifact_id, index, temporary, final_path = self._begin_registration(
            artifact_id, suffix
        )
        try:
            with source.open("rb") as incoming, temporary.open("wb") as destination:
                shutil.copyfileobj(incoming, destination, length=64 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            return self._finish_registration(
                artifact_id=artifact_id,
                index=index,
                temporary=temporary,
                final_path=final_path,
                addon_id=addon_id,
                version=version,
                addon_name=addon_name,
            )
        finally:
            self._cleanup_temporary(temporary)

    @_global_locked
    def register_bytes(
        self,
        *,
        data: bytes,
        filename: str = "upload.zip",
        addon_id: str | None = None,
        version: str | None = None,
        addon_name: str | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        suffix = Path(str(filename or "")).suffix or ".zip"
        artifact_id, index, temporary, final_path = self._begin_registration(
            artifact_id, suffix
        )
        try:
            with temporary.open("wb") as destination:
                destination.write(bytes(data))
                destination.flush()
                os.fsync(destination.fileno())
            return self._finish_registration(
                artifact_id=artifact_id,
                index=index,
                temporary=temporary,
                final_path=final_path,
                addon_id=addon_id,
                version=version,
                addon_name=addon_name,
            )
        finally:
            self._cleanup_temporary(temporary)

    @_global_locked
    def register_filelike(
        self,
        *,
        fileobj,
        filename: str = "upload.zip",
        addon_id: str | None = None,
        version: str | None = None,
        addon_name: str | None = None,
        artifact_id: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> ArtifactRecord:
        suffix = Path(str(filename or "")).suffix or ".zip"
        artifact_id, index, temporary, final_path = self._begin_registration(
            artifact_id, suffix
        )
        try:
            with temporary.open("wb") as destination:
                while True:
                    chunk = fileobj.read(chunk_size)
                    if not chunk:
                        break
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            return self._finish_registration(
                artifact_id=artifact_id,
                index=index,
                temporary=temporary,
                final_path=final_path,
                addon_id=addon_id,
                version=version,
                addon_name=addon_name,
            )
        finally:
            self._cleanup_temporary(temporary)

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
            addon_name=raw.get("addon_name") if isinstance(raw.get("addon_name"), str) else None,
        )
