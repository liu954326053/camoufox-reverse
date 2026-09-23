"""Project and session boundaries for reverse-analysis captures."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union


PathLike = Union[str, os.PathLike[str]]
_SESSION_ID = re.compile(r"[0-9a-f]{32}\Z")


class ProjectError(RuntimeError):
    """Raised when a reverse-analysis project or session is invalid."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _has_mode(path: Path, mask: int) -> bool:
    """Check permission bits as well as effective access for non-root callers."""
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ProjectError(f"Cannot inspect directory: {path}") from exc
    return bool(mode & mask) and os.access(path, os.R_OK | os.X_OK)


def _is_writable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ProjectError(f"Cannot inspect directory: {path}") from exc
    return bool(mode & stat.S_IWUSR | mode & stat.S_IWGRP | mode & stat.S_IWOTH) and os.access(
        path, os.W_OK
    )


def _secure_directory(path: Path) -> None:
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise ProjectError(f"Cannot secure directory: {path}") from exc


def _fsync_directory(path: Path) -> None:
    """Best-effort durability for the directory entry installed by replace."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor != -1:
                os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise ProjectError(f"Cannot write session manifest: {path}") from exc


class ReverseProject:
    """A filesystem root containing isolated reverse-analysis sessions."""

    def __init__(self, path: Path):
        self.path = path
        self.runs_dir = path / "runs"
        self.indexes_dir = path / "indexes"

    @classmethod
    def open(cls, project_dir: PathLike) -> "ReverseProject":
        """Open or create a project without creating missing parent directories."""
        try:
            raw_path = os.fspath(project_dir)
        except TypeError as exc:
            raise ProjectError("Project path must be path-like") from exc
        if isinstance(raw_path, str) and not raw_path.strip():
            raise ProjectError("Project path must be non-empty")

        requested = Path(raw_path)
        if requested.is_symlink():
            raise ProjectError(f"Project path cannot be a final symlink: {requested}")
        parent = requested.parent
        if not parent.exists() or not parent.is_dir():
            raise ProjectError(f"Project parent does not exist: {parent}")
        if not _has_mode(parent, stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise ProjectError(f"Project parent is not readable: {parent}")
        if requested.exists() and not requested.is_dir():
            raise ProjectError(f"Project path is not a directory: {requested}")
        if not requested.exists() and not _is_writable(parent):
            raise ProjectError(f"Project parent is not writable: {parent}")

        resolved = requested.resolve(strict=False)
        try:
            resolved.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise ProjectError(f"Cannot create project directory: {resolved}") from exc
        if not resolved.is_dir() or not _has_mode(
            resolved, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        ):
            raise ProjectError(f"Project directory is not readable: {resolved}")
        if not _is_writable(resolved):
            raise ProjectError(f"Project directory is not writable: {resolved}")
        _secure_directory(resolved)

        for child in (resolved / "runs", resolved / "indexes"):
            if child.is_symlink():
                raise ProjectError(f"Project directory cannot be a symlink: {child}")
            try:
                child.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise ProjectError(f"Cannot create project directory: {child}") from exc
            if not child.is_dir() or child.resolve(strict=True).parent != resolved:
                raise ProjectError(f"Project path is not a directory: {child}")
            _secure_directory(child)

        return cls(resolved)

    def create_session(self, resume_session: str | None = None) -> "ReverseSession":
        if resume_session is not None:
            return self._resume_session(resume_session)

        for _ in range(10):
            session_id = uuid.uuid4().hex
            session_path = self.runs_dir / session_id
            try:
                session_path.mkdir(mode=0o700)
            except FileExistsError:
                continue
            except OSError as exc:
                raise ProjectError("Cannot create session directory") from exc
            try:
                if session_path.is_symlink() or session_path.resolve(strict=True).parent != self.runs_dir:
                    raise ProjectError("Session directory is outside the project")
                raw_dir = session_path / "raw"
                raw_dir.mkdir(mode=0o700)
                if raw_dir.is_symlink() or raw_dir.resolve(strict=True).parent != session_path:
                    raise ProjectError("Session raw directory is outside the session")
                session = ReverseSession._new(self, session_id, session_path, raw_dir)
                _write_manifest(session.manifest_path, session._manifest)
                return session
            except OSError as exc:
                shutil.rmtree(session_path, ignore_errors=True)
                raise ProjectError("Cannot create session raw directory") from exc
            except Exception:
                shutil.rmtree(session_path, ignore_errors=True)
                raise
        raise ProjectError("Cannot allocate a unique session id")

    def _resume_session(self, session_id: str) -> "ReverseSession":
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ProjectError("Invalid session id")
        if self.runs_dir.is_symlink() or not self.runs_dir.is_dir():
            raise ProjectError("Project runs directory is invalid")
        session_path = self.runs_dir / session_id
        if session_path.is_symlink() or not session_path.is_dir():
            raise ProjectError("Session directory is invalid")
        if session_path.resolve(strict=True).parent != self.runs_dir.resolve(strict=True):
            raise ProjectError("Session directory is outside the project")
        manifest_path = session_path / "manifest.json"
        manifest = self._read_manifest(manifest_path, session_id)
        if manifest.get("status") != "incomplete":
            raise ProjectError("Only incomplete sessions can be resumed")
        raw_dir = session_path / "raw"
        if (
            raw_dir.is_symlink()
            or not raw_dir.is_dir()
            or raw_dir.resolve(strict=True).parent != session_path.resolve(strict=True)
        ):
            raise ProjectError("Session evidence directory is incomplete")

        manifest["status"] = "starting"
        manifest["ended_at"] = None
        _write_manifest(manifest_path, manifest)
        return ReverseSession(self, session_id, session_path, raw_dir, manifest)

    @staticmethod
    def _read_manifest(path: Path, session_id: str) -> dict[str, Any]:
        if path.is_symlink():
            raise ProjectError("Session manifest cannot be a symlink")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError("Cannot read session manifest") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != 1
            or manifest.get("session_id") != session_id
        ):
            raise ProjectError("Session manifest identity is invalid")
        return manifest


class ReverseSession:
    """An isolated capture session and its lifecycle manifest."""

    def __init__(
        self,
        project: ReverseProject,
        session_id: str,
        path: Path,
        raw_dir: Path,
        manifest: dict[str, Any],
    ):
        self.project = project
        self.session_id = session_id
        self.path = path
        self.raw_dir = raw_dir
        self.manifest_path = path / "manifest.json"
        self._manifest = manifest

    @classmethod
    def _new(
        cls, project: ReverseProject, session_id: str, path: Path, raw_dir: Path
    ) -> "ReverseSession":
        manifest = {
            "schema": 1,
            "session_id": session_id,
            "status": "starting",
            "capture_mode": "raw",
            "contains_sensitive_data": True,
            "telemetry": False,
            "started_at": _timestamp(),
            "ended_at": None,
            "event_loss": 0,
            "artifacts": [],
        }
        return cls(project, session_id, path, raw_dir, manifest)

    def close(self, status: str = "complete") -> None:
        if status not in {"complete", "incomplete"}:
            raise ProjectError("Invalid session final status")
        manifest = self.project._read_manifest(self.manifest_path, self.session_id)
        if manifest.get("status") == "complete":
            raise ProjectError("Complete sessions are immutable")
        manifest["status"] = status
        manifest["ended_at"] = _timestamp()
        _write_manifest(self.manifest_path, manifest)
        self._manifest = manifest

    def mark_incomplete(self, error: BaseException | None = None) -> None:
        del error
        self.close(status="incomplete")
