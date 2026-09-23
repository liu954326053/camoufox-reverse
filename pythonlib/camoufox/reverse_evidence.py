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
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


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
        if fcntl is None or os.open not in getattr(os, "supports_dir_fd", set()):
            raise EvidenceError("Secure evidence storage requires POSIX dirfd support")
        self.session = session
        self.root = session.path.resolve(strict=True)
        self.raw_dir = session.raw_dir.resolve(strict=True)
        if self.raw_dir.parent != self.root or not self.raw_dir.is_dir():
            raise EvidenceError("Session raw directory is invalid")
        try:
            self._root_fd = os.open(self.root, _DIR_FLAGS)
        except OSError as exc:
            raise EvidenceError("Cannot open session root securely") from exc
        self._lock_path = self.root / ".evidence.lock"

    def __del__(self):
        descriptor = getattr(self, "_root_fd", None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._root_fd = None

    def _manifest(self) -> dict[str, Any]:
        try:
            return reverse_project.ReverseProject._read_manifest(
                self.session.manifest_path, self.session.session_id
            )
        except reverse_project.ProjectError as exc:
            raise EvidenceError("Cannot read session manifest") from exc

    def _ensure_open(self) -> None:
        if self._manifest().get("status") == "complete":
            raise EvidenceError("Complete sessions are immutable")

    @staticmethod
    def _parts(relative_path: PathLike) -> tuple[str, tuple[str, ...]]:
        try:
            raw_path = os.fspath(relative_path)
        except TypeError as exc:
            raise EvidenceError("Artifact path must be path-like") from exc
        candidate = Path(raw_path)
        if not raw_path or candidate.is_absolute():
            raise EvidenceError("Artifact path must stay inside the session")
        parts = candidate.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise EvidenceError("Artifact path must stay inside the session")
        return candidate.as_posix(), parts

    def _open_directory(self, parent_fd: int, name: str, create: bool) -> int:
        try:
            descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            if not create:
                raise EvidenceError(f"Evidence directory does not exist: {name}")
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            try:
                descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
            except OSError as exc:
                raise EvidenceError(f"Cannot open evidence directory: {name}") from exc
        except OSError as exc:
            raise EvidenceError(f"Cannot open evidence directory: {name}") from exc
        try:
            os.fchmod(descriptor, stat.S_IRWXU)
        except OSError as exc:
            os.close(descriptor)
            raise EvidenceError(f"Cannot secure evidence directory: {name}") from exc
        return descriptor

    @contextmanager
    def _parent_fd(self, parts: tuple[str, ...], create: bool) -> Iterator[int]:
        descriptor = os.dup(self._root_fd)
        try:
            for name in parts[:-1]:
                child = self._open_directory(descriptor, name, create)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    def _open_relative(
        self,
        parts: tuple[str, ...],
        flags: int,
        mode: int = 0o600,
        *,
        create_parents: bool = False,
        allow_missing: bool = False,
    ) -> int | None:
        with self._parent_fd(parts, create_parents) as parent_fd:
            try:
                return os.open(
                    parts[-1], flags | _NOFOLLOW, mode, dir_fd=parent_fd
                )
            except FileNotFoundError:
                if allow_missing:
                    return None
                raise EvidenceError(f"Evidence file does not exist: {'/'.join(parts)}")
            except OSError as exc:
                raise EvidenceError(f"Cannot open evidence file: {'/'.join(parts)}") from exc

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
        normalized, parts = self._parts(relative_path)
        descriptor = self._open_relative(
            parts,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            create_parents=True,
        )
        assert descriptor is not None
        try:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            with _locked(descriptor):
                self._write_all(descriptor, data)
                try:
                    os.fsync(descriptor)
                except OSError as exc:
                    raise EvidenceError("Cannot fsync evidence file") from exc
        finally:
            os.close(descriptor)
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
        descriptor = self._open_relative(
            (".evidence.lock",), os.O_RDWR | os.O_CREAT
        )
        assert descriptor is not None
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        return descriptor

    def _open_existing(self, parts: tuple[str, ...]) -> int | None:
        return self._open_relative(parts, os.O_RDONLY, allow_missing=True)

    @staticmethod
    def _read_fd(descriptor: int) -> bytes:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        except OSError as exc:
            raise EvidenceError("Cannot read evidence file") from exc

    @staticmethod
    def _digest_fd(descriptor: int) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        except OSError as exc:
            raise EvidenceError("Cannot hash evidence file") from exc
        return size, digest.hexdigest()

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
        """Register metadata after validating the current regular file contents."""
        self._ensure_open()
        normalized, parts = self._parts(relative_path)
        record = self._registration(normalized, sha256, size, kind)
        lock = self._open_lock()
        try:
            with _locked(lock):
                manifest = self._manifest()
                if manifest.get("status") == "complete":
                    raise EvidenceError("Complete sessions are immutable")
                descriptor = self._open_existing(parts)
                if descriptor is None:
                    raise EvidenceError("Registered artifact does not exist")
                try:
                    file_stat = os.fstat(descriptor)
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise EvidenceError("Registered artifact must be a regular file")
                    with _locked(descriptor):
                        actual_size, actual_hash = self._digest_fd(descriptor)
                        if actual_size != size or actual_hash != sha256:
                            raise EvidenceError(
                                "Registered artifact metadata does not match file contents"
                            )
                        payload = json.dumps(
                            record, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8") + b"\n"
                        self._append(_REGISTRY_PATH, payload)
                        artifacts = manifest.setdefault("artifacts", [])
                        if not isinstance(artifacts, list):
                            raise EvidenceError("Session artifact registry is invalid")
                        artifacts.append(record)
                        try:
                            reverse_project._write_manifest(
                                self.session.manifest_path, manifest
                            )
                        except Exception as exc:
                            raise EvidenceError(
                                "Registry durable; manifest reconciliation required"
                            ) from exc
                finally:
                    os.close(descriptor)
        finally:
            os.close(lock)
        return record

    def _read_registry(self) -> list[dict[str, Any]]:
        parts = tuple(_REGISTRY_PATH.split("/"))
        descriptor = self._open_existing(parts)
        if descriptor is None:
            return []
        try:
            payload = self._read_fd(descriptor)
        finally:
            os.close(descriptor)
        try:
            text = payload.decode("utf-8")
            records = []
            for line in text.splitlines():
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                        raise ValueError("invalid registry record")
                    records.append(value)
            return records
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise EvidenceError("Cannot read raw artifact registry") from exc

    def _raw_files(self) -> list[tuple[str, tuple[str, ...]]]:
        files = []
        for path in sorted(self.raw_dir.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative == _REGISTRY_PATH or path.name.startswith("."):
                continue
            files.append((relative, tuple(relative.split("/"))))
        return files

    def _file_info(self, parts: tuple[str, ...]) -> tuple[int, str]:
        descriptor = self._open_existing(parts)
        if descriptor is None:
            raise EvidenceError("Raw evidence file disappeared during indexing")
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise EvidenceError("Raw evidence path is not a regular file")
            return self._digest_fd(descriptor)
        finally:
            os.close(descriptor)

    def _event_info(self, parts: tuple[str, ...]) -> tuple[int, int, list[int]]:
        if not parts[-1].endswith(".jsonl"):
            return 0, 0, []
        descriptor = self._open_existing(parts)
        if descriptor is None:
            raise EvidenceError("JSONL evidence file disappeared during indexing")
        try:
            payload = self._read_fd(descriptor)
        finally:
            os.close(descriptor)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return 0, 1, []
        count = malformed = 0
        sequences: list[int] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(value, dict):
                malformed += 1
                continue
            count += 1
            sequence = value.get("sequence")
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                sequences.append(sequence)
        return count, malformed, sequences

    def rebuild_index(self) -> Path:
        """Rebuild an incomplete-session index and reconcile its manifest."""
        manifest = self._manifest()
        if manifest.get("status") == "complete":
            raise EvidenceError("Complete sessions are immutable")
        records = self._read_registry()
        if manifest.get("artifacts") != records:
            manifest["artifacts"] = records
            try:
                reverse_project._write_manifest(self.session.manifest_path, manifest)
            except Exception as exc:
                raise EvidenceError("Cannot reconcile session artifact manifest") from exc

        registered: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            registered.setdefault(record["path"], []).append(record)
        files: dict[str, dict[str, Any]] = {}
        artifacts: list[dict[str, Any]] = []
        malformed_total = 0
        for relative, parts in self._raw_files():
            byte_count, content_hash = self._file_info(parts)
            event_count, malformed, sequences = self._event_info(parts)
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
                    "kind": "jsonl" if relative.endswith(".jsonl") else "raw",
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
                    {
                        "bytes": 0,
                        "event_count": 0,
                        "content_sha256": None,
                        "event_sequence": None,
                    }
                )
                artifacts.append(item)
        artifacts.sort(
            key=lambda item: (
                item.get("path", ""),
                item.get("event_sequence") is None,
                item.get("event_sequence") if item.get("event_sequence") is not None else 0,
                item.get("kind", ""),
                item.get("size", 0),
                item.get("sha256", ""),
                item.get("content_sha256") or "",
                item.get("bytes", 0),
                item.get("event_count", 0),
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )
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
        """Fsync raw files and registry before session shutdown."""
        self._ensure_open()
        paths = [parts for _, parts in self._raw_files()]
        paths.append(tuple(_REGISTRY_PATH.split("/")))
        for parts in paths:
            descriptor = self._open_existing(parts)
            if descriptor is None:
                continue
            try:
                os.fsync(descriptor)
            except OSError as exc:
                raise EvidenceError("Cannot fsync evidence file") from exc
            finally:
                os.close(descriptor)
        _fsync_directory(self.raw_dir)
        _fsync_directory(self.root)

    def finalize(self, status: str = "complete") -> Path:
        """Flush, build the index, then finalize the associated session."""
        self.flush()
        index_path = self.rebuild_index()
        try:
            self.session.close(status=status)
        except reverse_project.ProjectError as exc:
            raise EvidenceError("Cannot finalize evidence session") from exc
        return index_path

    def close(self, status: str = "complete") -> Path:
        """Compatibility alias for :meth:`finalize`."""
        return self.finalize(status=status)

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.finalize()
        else:
            try:
                self.flush()
            finally:
                self.session.mark_incomplete(exc_value)
