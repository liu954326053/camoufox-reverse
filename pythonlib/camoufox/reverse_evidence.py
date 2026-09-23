"""Lossless raw evidence storage for reverse-analysis sessions."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Union

from . import reverse_project
from .reverse_project import ReverseSession

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


PathLike = Union[str, os.PathLike[str]]
_REGISTRY_PATH = "raw/artifacts.jsonl"


class EvidenceError(RuntimeError):
    """Raised when evidence cannot be safely written or indexed."""


def _fsync_directory(path: Path) -> None:
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


@contextmanager
def _locked(descriptor: int) -> Iterator[None]:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)


class EvidenceStore:
    """Append-only raw evidence and a rebuildable deterministic session index."""

    def __init__(self, session: ReverseSession):
        if not isinstance(session, ReverseSession):
            raise TypeError("session must be a ReverseSession")
        self.session = session
        self.root = session.path.resolve(strict=True)
        self.raw_dir = session.raw_dir.resolve(strict=True)
        if self.raw_dir.parent != self.root or not self.raw_dir.is_dir():
            raise EvidenceError("Session raw directory is invalid")
        self._lock_path = self.root / ".evidence.lock"

    def _ensure_open(self) -> None:
        try:
            manifest = reverse_project.ReverseProject._read_manifest(
                self.session.manifest_path, self.session.session_id
            )
        except reverse_project.ProjectError as exc:
            raise EvidenceError("Cannot read session manifest") from exc
        if manifest.get("status") == "complete":
            raise EvidenceError("Complete sessions are immutable")

    def _path(self, relative_path: PathLike) -> tuple[str, Path]:
        try:
            raw_path = os.fspath(relative_path)
        except TypeError as exc:
            raise EvidenceError("Artifact path must be path-like") from exc
        candidate = Path(raw_path)
        if not raw_path or candidate.is_absolute() or ".." in candidate.parts:
            raise EvidenceError("Artifact path must stay inside the session")
        if not candidate.parts or candidate == Path("."):
            raise EvidenceError("Artifact path must be non-empty")
        current = self.root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise EvidenceError("Artifact path cannot contain symlinks")
        resolved = (self.root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise EvidenceError("Artifact path escapes the session") from exc
        return candidate.as_posix(), resolved

    @staticmethod
    def _make_parents(path: Path, root: Path) -> None:
        current = root
        for part in path.parent.relative_to(root).parts:
            current /= part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise EvidenceError("Artifact parent is not a directory")
            current.mkdir(mode=0o700, exist_ok=True)
            try:
                current.chmod(0o700)
            except OSError as exc:
                raise EvidenceError(f"Cannot secure artifact directory: {current}") from exc

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            try:
                written = os.write(descriptor, data[offset:])
            except OSError as exc:
                raise EvidenceError("Cannot append evidence bytes") from exc
            if written <= 0:
                raise EvidenceError("Evidence write made no progress")
            offset += written

    def _append(self, relative_path: PathLike, data: bytes) -> str:
        self._ensure_open()
        normalized, path = self._path(relative_path)
        self._make_parents(path, self.root)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise EvidenceError(f"Cannot open evidence file: {path}") from exc
        try:
            with _locked(descriptor):
                self._write_all(descriptor, data)
                try:
                    os.fsync(descriptor)
                except OSError as exc:
                    raise EvidenceError("Cannot fsync evidence file") from exc
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
        return normalized

    def append_jsonl(self, relative_path: PathLike, event: Mapping[str, Any]) -> str:
        """Append one complete JSON object without lossy Unicode conversion."""
        if not isinstance(event, Mapping):
            raise EvidenceError("JSONL events must be JSON objects")
        try:
            serialized = json.dumps(
                dict(event), ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
            payload = serialized.encode("utf-8", errors="backslashreplace") + b"\n"
        except (TypeError, ValueError, UnicodeError) as exc:
            raise EvidenceError("Event is not JSON serializable") from exc
        return self._append(relative_path, payload)

    def append_bytes(self, relative_path: PathLike, data: bytes) -> str:
        """Append bytes without decoding, redaction, truncation, or conversion."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise EvidenceError("Evidence data must be bytes-like")
        return self._append(relative_path, bytes(data))

    def _open_lock(self) -> int:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            return descriptor
        except OSError as exc:
            raise EvidenceError("Cannot open evidence lock") from exc

    @staticmethod
    def _registration(path: str, sha256: str, size: int, kind: str) -> dict[str, Any]:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise EvidenceError("sha256 must be a lowercase hexadecimal digest")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise EvidenceError("Artifact size must be a non-negative integer")
        if not isinstance(kind, str) or not kind:
            raise EvidenceError("Artifact kind must be non-empty")
        return {"path": path, "sha256": sha256, "size": size, "kind": kind}

    def register_artifact(
        self, relative_path: PathLike, sha256: str, size: int, kind: str
    ) -> dict[str, Any]:
        """Append artifact metadata to the raw registry and session manifest."""
        self._ensure_open()
        normalized, path = self._path(relative_path)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise EvidenceError("Registered artifact must be a regular file")
        record = self._registration(normalized, sha256, size, kind)
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ) + b"\n"
        lock = self._open_lock()
        try:
            with _locked(lock):
                try:
                    manifest = reverse_project.ReverseProject._read_manifest(
                        self.session.manifest_path, self.session.session_id
                    )
                    if manifest.get("status") == "complete":
                        raise EvidenceError("Complete sessions are immutable")
                    artifacts = manifest.setdefault("artifacts", [])
                    if not isinstance(artifacts, list):
                        raise EvidenceError("Session artifact registry is invalid")
                    self._append(_REGISTRY_PATH, payload)
                    artifacts.append(record)
                    reverse_project._write_manifest(self.session.manifest_path, manifest)
                except reverse_project.ProjectError as exc:
                    raise EvidenceError("Cannot update session artifact registry") from exc
        finally:
            os.close(lock)
        return record

    def _read_registry(self) -> list[dict[str, Any]]:
        path = self.root / _REGISTRY_PATH
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        value = json.loads(line)
                        if isinstance(value, dict) and isinstance(value.get("path"), str):
                            records.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("Cannot read raw artifact registry") from exc
        return records

    @staticmethod
    def _digest(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise EvidenceError(f"Cannot read raw evidence file: {path}") from exc
        return size, digest.hexdigest()

    @staticmethod
    def _events(path: Path) -> tuple[int, int, list[int]]:
        if path.suffix != ".jsonl":
            return 0, 0, []
        count = malformed = 0
        sequences: list[int] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except (UnicodeError, json.JSONDecodeError):
                        malformed += 1
                        continue
                    if not isinstance(value, dict):
                        malformed += 1
                        continue
                    count += 1
                    sequence = value.get("sequence")
                    if isinstance(sequence, int) and not isinstance(sequence, bool):
                        sequences.append(sequence)
        except OSError as exc:
            raise EvidenceError(f"Cannot read JSONL evidence file: {path}") from exc
        return count, malformed, sequences

    def _raw_files(self) -> list[tuple[str, Path]]:
        files = []
        for path in sorted(self.raw_dir.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative == _REGISTRY_PATH or path.name.startswith("."):
                continue
            files.append((relative, path))
        return files

    def rebuild_index(self) -> Path:
        """Rebuild an index from disk, including incomplete sessions."""
        records = self._read_registry()
        registered: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            registered.setdefault(record["path"], []).append(record)
        files: dict[str, dict[str, Any]] = {}
        artifacts: list[dict[str, Any]] = []
        malformed_total = 0
        for relative, path in self._raw_files():
            byte_count, content_hash = self._digest(path)
            event_count, malformed, sequences = self._events(path)
            malformed_total += malformed
            files[relative] = {
                "bytes": byte_count,
                "event_count": event_count,
                "sha256": content_hash,
            }
            entries = registered.get(relative) or [
                {
                    "path": relative,
                    "sha256": content_hash,
                    "size": byte_count,
                    "kind": "jsonl" if path.suffix == ".jsonl" else "raw",
                }
            ]
            for entry in entries:
                item = dict(entry)
                item.update(
                    {
                        "bytes": byte_count,
                        "event_count": event_count,
                        "content_sha256": content_hash,
                        "event_sequence": min(sequences) if sequences else None,
                    }
                )
                artifacts.append(item)
        for relative, entries in registered.items():
            if relative in files:
                continue
            for entry in entries:
                item = dict(entry)
                item.update(
                    {"bytes": 0, "event_count": 0, "content_sha256": None, "event_sequence": None}
                )
                artifacts.append(item)
        artifacts.sort(
            key=lambda item: (
                item.get("path", ""),
                item.get("event_sequence") is None,
                item.get("event_sequence") if item.get("event_sequence") is not None else 0,
                item.get("sha256", ""),
            )
        )
        try:
            manifest = reverse_project.ReverseProject._read_manifest(
                self.session.manifest_path, self.session.session_id
            )
        except reverse_project.ProjectError as exc:
            raise EvidenceError("Cannot read session manifest") from exc
        index = {
            "schema": 1,
            "session_id": self.session.session_id,
            "event_loss": int(manifest.get("event_loss", 0)) + malformed_total,
            "artifacts": artifacts,
            "files": {key: files[key] for key in sorted(files)},
        }
        index_path = self.session.project.indexes_dir / f"{self.session.session_id}.json"
        temporary = index_path.with_name(f".{index_path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    descriptor = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if descriptor != -1:
                    os.close(descriptor)
            os.replace(temporary, index_path)
            _fsync_directory(index_path.parent)
        except OSError as exc:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise EvidenceError("Cannot write deterministic session index") from exc
        return index_path

    def flush(self) -> None:
        """Fsync raw files and directories before session shutdown."""
        self._ensure_open()
        for _, path in self._raw_files():
            try:
                descriptor = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise EvidenceError(f"Cannot fsync evidence file: {path}") from exc
        _fsync_directory(self.raw_dir)
        _fsync_directory(self.root)

    def close(self, status: str = "complete") -> Path:
        self.flush()
        index_path = self.rebuild_index()
        try:
            self.session.close(status=status)
        except reverse_project.ProjectError as exc:
            raise EvidenceError("Cannot finalize evidence session") from exc
        return index_path

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            try:
                self.flush()
            finally:
                self.session.mark_incomplete(exc_value)
