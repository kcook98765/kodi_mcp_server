"""Pure repository mutation helpers for kodi_mcp_server.

Artifact flow:
- source addon packages: `kodi_addon/packages/...`
- built compatibility zip artifacts: `addon/*.zip`
- authoritative published repo content: `repo/dev-repo/...`
"""

import hashlib
import os
import re
import stat
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from .artifacts import AddonArtifact
from .orchestration_locking import acquire_global_workflow_lock

PUBLICATION_COPY_CHUNK_SIZE = 1024 * 1024


@dataclass
class _RollbackBackup:
    path: Path
    mode: int


class RepositoryPublicationError(RuntimeError):
    error_code = "REPOSITORY_PUBLICATION_ROLLBACK_FAILED"

    def __init__(self, replaced_count: int, rollback_failure_count: int):
        self.replaced_count = replaced_count
        self.rollback_failure_count = rollback_failure_count
        super().__init__(
            "repository publication failed and rollback could not restore all finals"
        )


def _global_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with acquire_global_workflow_lock():
            return method(self, *args, **kwargs)

    return locked


def _write_fsynced_temp(final_path: Path, data: bytes) -> Path:
    """Write complete bytes beside the final and return the fsynced temp path."""

    final_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".publish-", dir=final_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        return temporary
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _copy_stream_bounded(source, destination) -> None:
    while True:
        chunk = source.read(PUBLICATION_COPY_CHUNK_SIZE)
        if not chunk:
            return
        destination.write(chunk)


def _copy_path_to_fsynced_temp(
    source_path: Path,
    *,
    destination_dir: Path,
    prefix: str,
    suffix: str,
    mode: int,
) -> Path:
    """Copy one archive to an exclusive disk temp using a fixed buffer."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=prefix, suffix=suffix, dir=destination_dir
    )
    temporary = Path(temporary_name)
    try:
        with source_path.open("rb") as source, os.fdopen(fd, "wb") as destination:
            _copy_stream_bounded(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, mode)
        _fsync_file(temporary)
        _fsync_directory(destination_dir)
        return temporary
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _fsync_publication_directories(repo_zip_path: Path, dev_repo: Path) -> None:
    _fsync_directory(repo_zip_path.parent)
    if repo_zip_path.parent.parent != dev_repo:
        _fsync_directory(repo_zip_path.parent.parent)
    if repo_zip_path.parent != dev_repo:
        _fsync_directory(dev_repo)


class RepoPublisher:
    """Low-level repository mutation/build operations."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.dev_repo = self.repo_root / "dev-repo"
        self.zips_dir = self.dev_repo / "zips"

    @_global_locked
    def publish_addon_artifact(self, artifact: AddonArtifact) -> dict:
        """Publish one complete ZIP/XML/MD5 generation under the workflow lock.

        Each final file is replaced atomically. XML and MD5 are still two files,
        so consistency-sensitive readers must hold the same global lock or consume
        an immutable repository snapshot.
        """

        addon_zip_path = artifact.legacy_build_zip_path
        if not addon_zip_path.exists():
            raise FileNotFoundError(f"Addon zip file not found: {addon_zip_path}")

        with zipfile.ZipFile(addon_zip_path) as archive:
            addon_root = ET.fromstring(archive.read(f"{artifact.addon_id}/addon.xml"))
            new_addon_entry = ET.tostring(addon_root, encoding="unicode")

        addons_xml_path = self.dev_repo / "addons.xml"
        if addons_xml_path.exists():
            addons_xml_content = addons_xml_path.read_text(encoding="utf-8")
        else:
            addons_xml_content = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                "<addons>\n"
                "</addons>\n"
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
                "</addons>", f"\n{new_addon_entry}\n</addons>"
            )
            action = "added"

        xml_bytes = addons_xml_content.encode("utf-8")
        md5_checksum = hashlib.md5(xml_bytes).hexdigest()
        md5_bytes = f"{md5_checksum}  addons.xml\n".encode("utf-8")
        repo_zip_path = artifact.repo_zip_path
        md5_path = self.dev_repo / "addons.xml.md5"
        metadata_paths = (addons_xml_path, md5_path)
        prior_metadata = {
            path: (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
            )
            if path.exists()
            else None
            for path in metadata_paths
        }

        rollback_root = self.repo_root / ".publication-rollbacks"
        rollback_backup: _RollbackBackup | None = None
        if repo_zip_path.exists():
            prior_zip_mode = stat.S_IMODE(repo_zip_path.stat().st_mode)
            rollback_backup = _RollbackBackup(
                path=_copy_path_to_fsynced_temp(
                    repo_zip_path,
                    destination_dir=rollback_root,
                    prefix=".rollback-",
                    suffix=".zip",
                    mode=prior_zip_mode,
                ),
                mode=prior_zip_mode,
            )

        temporaries: list[Path] = []
        replaced: list[Path] = []
        preserve_backup = False
        try:
            temporary_zip = _copy_path_to_fsynced_temp(
                addon_zip_path,
                destination_dir=repo_zip_path.parent,
                prefix=".publish-",
                suffix=".zip",
                mode=0o644,
            )
            temporaries.append(temporary_zip)
            temporary_xml = _write_fsynced_temp(addons_xml_path, xml_bytes)
            temporaries.append(temporary_xml)
            temporary_md5 = _write_fsynced_temp(md5_path, md5_bytes)
            temporaries.append(temporary_md5)

            os.replace(temporary_zip, repo_zip_path)
            temporaries.remove(temporary_zip)
            replaced.append(repo_zip_path)
            os.replace(temporary_xml, addons_xml_path)
            temporaries.remove(temporary_xml)
            replaced.append(addons_xml_path)
            os.replace(temporary_md5, md5_path)
            temporaries.remove(temporary_md5)
            replaced.append(md5_path)
            _fsync_publication_directories(repo_zip_path, self.dev_repo)
        except Exception as publication_error:
            rollback_failures = 0
            for final_path in reversed(replaced):
                try:
                    if final_path == repo_zip_path:
                        if rollback_backup is None:
                            final_path.unlink(missing_ok=True)
                        else:
                            os.chmod(rollback_backup.path, rollback_backup.mode)
                            _fsync_file(rollback_backup.path)
                            os.replace(rollback_backup.path, final_path)
                            rollback_backup = None
                    else:
                        old_state = prior_metadata[final_path]
                        if old_state is None:
                            final_path.unlink(missing_ok=True)
                        else:
                            old_bytes, old_mode = old_state
                            rollback_temp = _write_fsynced_temp(final_path, old_bytes)
                            temporaries.append(rollback_temp)
                            os.chmod(rollback_temp, old_mode)
                            _fsync_file(rollback_temp)
                            os.replace(rollback_temp, final_path)
                            temporaries.remove(rollback_temp)
                except Exception:
                    rollback_failures += 1
            try:
                _fsync_publication_directories(repo_zip_path, self.dev_repo)
            except Exception:
                rollback_failures += 1
            if rollback_failures:
                preserve_backup = rollback_backup is not None
                raise RepositoryPublicationError(
                    len(replaced), rollback_failures
                ) from publication_error
            raise
        finally:
            for temporary in temporaries:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            if rollback_backup is not None and not preserve_backup:
                try:
                    rollback_backup.path.unlink()
                    _fsync_directory(rollback_root)
                except FileNotFoundError:
                    pass

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
        artifact = AddonArtifact(
            addon_id=addon_id,
            addon_name=addon_name,
            addon_version=addon_version,
            provider_name=provider_name,
            repo_root=self.repo_root,
            build_root=Path(addon_zip_path).resolve().parent,
        )
        return self.publish_addon_artifact(artifact)
