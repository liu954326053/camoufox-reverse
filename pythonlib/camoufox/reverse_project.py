"""Project and session boundaries for reverse-analysis captures."""

from __future__ import annotations

from copy import deepcopy
import json
import os
import re
import shutil
import stat
import threading
import uuid
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union


PathLike = Union[str, os.PathLike[str]]
_SESSION_ID = re.compile(r"[0-9a-f]{32}\Z")
_SESSION_LOCKS: dict[str, threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()
_REQUIRED_MANIFEST_TYPES = {
    "schema": int,
    "session_id": str,
    "status": str,
    "capture_mode": str,
    "contains_sensitive_data": bool,
    "telemetry": bool,
    "started_at": str,
    "event_loss": int,
    "artifacts": list,
}


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


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _session_lock(path: Path):
    """Serialize lifecycle transitions in-process and across POSIX processes."""
    if path.is_symlink():
        raise ProjectError("Session directory cannot be a symlink")
    lock_path = path / ".lifecycle.lock"
    if lock_path.is_symlink():
        raise ProjectError("Session lock cannot be a symlink")
    thread_lock = _thread_lock_for(path)
    with thread_lock:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ProjectError("Cannot acquire session lock") from exc
        try:
            try:
                import fcntl
            except ImportError:
                fcntl = None
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
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

    def get_session(self, session_id: str) -> "ReverseSession":
        """Load an existing session without changing its lifecycle state."""
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ProjectError("Invalid session id")
        if self.runs_dir.is_symlink() or not self.runs_dir.is_dir():
            raise ProjectError("Project runs directory is invalid")
        session_path = self.runs_dir / session_id
        if session_path.is_symlink() or not session_path.is_dir():
            raise ProjectError("Session directory is invalid")
        if session_path.resolve(strict=True).parent != self.runs_dir.resolve(strict=True):
            raise ProjectError("Session directory is outside the project")
        manifest = self._read_manifest(session_path / "manifest.json", session_id)
        raw_dir = session_path / "raw"
        if (
            raw_dir.is_symlink()
            or not raw_dir.is_dir()
            or raw_dir.resolve(strict=True).parent != session_path.resolve(strict=True)
        ):
            raise ProjectError("Session evidence directory is invalid")
        return ReverseSession(self, session_id, session_path, raw_dir, manifest)

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return safe lifecycle metadata for every valid project session."""
        if self.runs_dir.is_symlink() or not self.runs_dir.is_dir():
            raise ProjectError("Project runs directory is invalid")
        sessions = []
        for session_path in sorted(self.runs_dir.iterdir(), key=lambda item: item.name):
            if not session_path.is_dir() or session_path.is_symlink():
                raise ProjectError("Session directory is invalid")
            session = self.get_session(session_path.name)
            manifest = session.manifest_snapshot()
            sessions.append(
                {
                    "session_id": session.session_id,
                    "status": manifest["status"],
                    "started_at": manifest["started_at"],
                    "ended_at": manifest["ended_at"],
                    "session_dir": str(session.path),
                    "manifest": str(session.manifest_path),
                }
            )
        return sessions

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
        with _session_lock(session_path):
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
        if not isinstance(manifest, dict):
            raise ProjectError("Session manifest identity is invalid")
        for field, expected_type in _REQUIRED_MANIFEST_TYPES.items():
            if field not in manifest or type(manifest[field]) is not expected_type:
                raise ProjectError("Session manifest shape is invalid")
        if "ended_at" not in manifest:
            raise ProjectError("Session manifest shape is invalid")
        if (
            manifest["schema"] != 1
            or manifest["session_id"] != session_id
            or manifest["status"] not in {"starting", "running", "incomplete", "complete"}
            or manifest["capture_mode"] != "raw"
            or not manifest["started_at"]
            or manifest["event_loss"] < 0
        ):
            raise ProjectError("Session manifest values are invalid")
        if manifest["ended_at"] is not None and type(manifest["ended_at"]) is not str:
            raise ProjectError("Session manifest shape is invalid")
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
        self.trace_dir = path / "trace"
        self.manifest_path = path / "manifest.json"
        self._manifest = manifest
        self._evidence_attached = False
        self._evidence_store_ref = None

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

    def _attach_evidence_store(self, store: Any) -> None:
        self._evidence_attached = True
        self._evidence_store_ref = weakref.ref(store)

    def _close_manifest_locked(self, status: str) -> None:
        manifest = self.project._read_manifest(self.manifest_path, self.session_id)
        if manifest.get("status") == "complete":
            raise ProjectError("Complete sessions are immutable")
        manifest["status"] = status
        manifest["ended_at"] = _timestamp()
        _write_manifest(self.manifest_path, manifest)
        self._manifest = manifest

    def _close_manifest(self, status: str) -> None:
        with _session_lock(self.path):
            self._close_manifest_locked(status)

    def _close_from_evidence(self, status: str) -> None:
        """Finalize the lifecycle after EvidenceStore made its durable snapshot."""
        if status not in {"complete", "incomplete"}:
            raise ProjectError("Invalid session final status")
        self._close_manifest_locked(status)

    def close(self, status: str = "complete") -> None:
        if status not in {"complete", "incomplete"}:
            raise ProjectError("Invalid session final status")
        if self._evidence_attached:
            store = self._evidence_store_ref() if self._evidence_store_ref else None
            if store is None:
                try:
                    from .reverse_evidence import EvidenceStore

                    store = EvidenceStore(self)
                except Exception as exc:
                    raise ProjectError("EvidenceStore is unavailable for session finalization") from exc
            try:
                store.finalize(status=status)
            except Exception as exc:
                if exc.__class__.__name__ == "EvidenceError":
                    raise ProjectError(str(exc)) from exc
                raise
            return
        # A caller may close a session without having explicitly constructed an
        # EvidenceStore. Create one lazily so a complete session always has a
        # durable, rebuildable index rather than only a manifest transition.
        try:
            from .reverse_evidence import EvidenceStore

            EvidenceStore(self).finalize(status=status)
        except Exception as exc:
            if exc.__class__.__name__ != "EvidenceError":
                raise ProjectError("EvidenceStore is unavailable for session finalization") from exc
            raise ProjectError(str(exc)) from exc

    def mark_running(
        self,
        *,
        trace_profile: str | None = None,
        browser_version: str | None = None,
        proxy: dict[str, Any] | None = None,
    ) -> None:
        """Transition a newly created or resumed session into ``running``."""
        with _session_lock(self.path):
            manifest = self.project._read_manifest(self.manifest_path, self.session_id)
            if manifest.get("status") != "starting":
                raise ProjectError("Only starting sessions can be marked running")
            manifest["status"] = "running"
            manifest["ended_at"] = None
            if trace_profile is not None:
                manifest["trace_profile"] = trace_profile
            if browser_version is not None:
                manifest["browser_version"] = browser_version
            if proxy is not None:
                manifest["proxy"] = proxy
            _write_manifest(self.manifest_path, manifest)
            self._manifest = manifest

    def manifest_snapshot(self) -> dict[str, Any]:
        """Return a validated copy of the current on-disk session manifest."""
        return deepcopy(self.project._read_manifest(self.manifest_path, self.session_id))

    def mark_incomplete(self, error: BaseException | None = None) -> None:
        del error
        self.close(status="incomplete")
