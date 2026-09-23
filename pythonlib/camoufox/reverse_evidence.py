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


class EvidenceStore:
    """Append-only raw evidence and a rebuildable deterministic session index."""

    def __init__(self, session: ReverseSession):
        if not isinstance(session, ReverseSession):
            raise TypeError("session must be a ReverseSession")
        if (
            fcntl is None
            or os.open not in getattr(os, "supports_dir_fd", set())
            or os.rename not in getattr(os, "supports_dir_fd", set())
        ):
            raise EvidenceError("Secure evidence storage requires POSIX dirfd support")
        self._root_fd = None
        self._project_fd = None
        self._indexes_fd = None
        self.session = session
        self.root = session.path.resolve(strict=True)
        self.raw_dir = session.raw_dir.resolve(strict=True)
        self.project_root = session.project.path.resolve(strict=True)
        if self.raw_dir.parent != self.root or not self.raw_dir.is_dir():
            raise EvidenceError("Session raw directory is invalid")
        try:
            self._root_fd = os.open(self.root, _DIR_FLAGS)
            self._project_fd = os.open(self.project_root, _DIR_FLAGS)
            self._indexes_fd = self._open_directory(self._project_fd, "indexes", create=False)
        except OSError as exc:
            self._close_descriptors()
            raise EvidenceError("Cannot open session root securely") from exc
        self._indexes_identity = self._identity(os.fstat(self._indexes_fd))
        session._attach_evidence_store(self)

    def _close_descriptors(self) -> None:
        for name in ("_indexes_fd", "_project_fd", "_root_fd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                setattr(self, name, None)
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def __del__(self):
        try:
            self._close_descriptors()
        except Exception:
            pass

    @contextmanager
    def _evidence_lock(self) -> Iterator[None]:
        """Serialize every evidence mutation and index snapshot for this session."""
        try:
            with reverse_project._session_lock(self.root):
                yield
        except reverse_project.ProjectError as exc:
            raise EvidenceError("Cannot acquire evidence session lock") from exc

    def _manifest(self) -> dict[str, Any]:
        try:
            return self.session.manifest_snapshot()
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

    @contextmanager
    def _opened_relative(
        self,
        parts: tuple[str, ...],
        flags: int,
        mode: int = 0o600,
        *,
        create_parents: bool = False,
    ) -> Iterator[tuple[int, int]]:
        with self._parent_fd(parts, create_parents) as parent_fd:
            try:
                descriptor = os.open(
                    parts[-1], flags | _NOFOLLOW, mode, dir_fd=parent_fd
                )
            except OSError as exc:
                raise EvidenceError(f"Cannot open evidence file: {'/'.join(parts)}") from exc
            try:
                yield descriptor, parent_fd
            finally:
                os.close(descriptor)

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

    def _append_unlocked(self, relative_path: PathLike, data: bytes) -> str:
        normalized, parts = self._parts(relative_path)
        descriptor = self._open_relative(
            parts,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            create_parents=True,
        )
        assert descriptor is not None
        try:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            self._write_all(descriptor, data)
            try:
                os.fsync(descriptor)
            except OSError as exc:
                raise EvidenceError("Cannot fsync evidence file") from exc
        finally:
            os.close(descriptor)
        return normalized

    def _append(self, relative_path: PathLike, data: bytes) -> str:
        with self._evidence_lock():
            self._ensure_open()
            return self._append_unlocked(relative_path, data)

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
    def _identity(file_stat: os.stat_result) -> tuple[int, int]:
        return file_stat.st_dev, file_stat.st_ino

    @classmethod
    def _same_file_observation(
        cls, first: os.stat_result, second: os.stat_result
    ) -> bool:
        return (
            cls._identity(first) == cls._identity(second)
            and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
            and first.st_size == second.st_size
            and first.st_mtime_ns == second.st_mtime_ns
            and first.st_ctime_ns == second.st_ctime_ns
        )

    @staticmethod
    def _parse_events(payload: bytes) -> tuple[int, int, list[int]]:
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
        normalized, parts = self._parts(relative_path)
        record = self._registration(normalized, sha256, size, kind)
        with self._evidence_lock():
            self._ensure_open()
            manifest = self._manifest()
            if manifest.get("status") == "complete":
                raise EvidenceError("Complete sessions are immutable")
            with self._opened_relative(parts, os.O_RDONLY) as (descriptor, parent_fd):
                before_path = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                before_fd = os.fstat(descriptor)
                if not stat.S_ISREG(before_fd.st_mode) or not stat.S_ISREG(before_path.st_mode):
                    raise EvidenceError("Registered artifact must be a regular file")
                if not self._same_file_observation(before_fd, before_path):
                    raise EvidenceError("Registered artifact was replaced during validation")
                actual_size, actual_hash = self._digest_fd(descriptor)
                after_fd = os.fstat(descriptor)
                after_path = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if not self._same_file_observation(before_fd, after_fd) or not self._same_file_observation(
                    before_fd, after_path
                ):
                    raise EvidenceError("Registered artifact was replaced during validation")
                if actual_size != size or actual_hash != sha256:
                    raise EvidenceError(
                        "Registered artifact metadata does not match file contents"
                    )
                payload = json.dumps(
                    record, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8") + b"\n"
                self._append_unlocked(_REGISTRY_PATH, payload)
                artifacts = manifest.setdefault("artifacts", [])
                if not isinstance(artifacts, list):
                    raise EvidenceError("Session artifact registry is invalid")
                artifacts.append(record)
                try:
                    reverse_project._write_manifest(self.session.manifest_path, manifest)
                except Exception as exc:
                    raise EvidenceError("Registry durable; manifest reconciliation required") from exc
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

    def _file_snapshot(
        self, relative: str, parts: tuple[str, ...]
    ) -> tuple[int, str, int, int, list[int]]:
        with self._opened_relative(parts, os.O_RDONLY) as (descriptor, parent_fd):
            before_path = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            before_fd = os.fstat(descriptor)
            if not stat.S_ISREG(before_fd.st_mode) or not stat.S_ISREG(before_path.st_mode):
                raise EvidenceError("Raw evidence path is not a regular file")
            if not self._same_file_observation(before_fd, before_path):
                raise EvidenceError(f"Raw evidence path was replaced: {relative}")
            if relative.endswith(".jsonl"):
                payload = self._read_fd(descriptor)
                byte_count = len(payload)
                content_hash = hashlib.sha256(payload).hexdigest()
                event_count, malformed, sequences = self._parse_events(payload)
            else:
                byte_count, content_hash = self._digest_fd(descriptor)
                event_count, malformed, sequences = 0, 0, []
            after_fd = os.fstat(descriptor)
            after_path = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            if not self._same_file_observation(before_fd, after_fd) or not self._same_file_observation(
                before_fd, after_path
            ):
                raise EvidenceError(f"Raw evidence path was replaced: {relative}")
            return byte_count, content_hash, event_count, malformed, sequences

    def _validate_indexes_directory(self) -> None:
        try:
            current = os.lstat(self.session.project.indexes_dir)
        except OSError as exc:
            raise EvidenceError("Session index directory is unavailable") from exc
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
            raise EvidenceError("Session index directory cannot be a symlink")
        if self._identity(current) != self._indexes_identity:
            raise EvidenceError("Session index directory was replaced")

    def _write_index(self, index: dict[str, Any]) -> Path:
        self._validate_indexes_directory()
        name = f"{self.session.session_id}.json"
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=False).encode(
            "utf-8"
        ) + b"\n"
        descriptor = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                0o600,
                dir_fd=self._indexes_fd,
            )
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            self._validate_indexes_directory()
            os.rename(
                temporary,
                name,
                src_dir_fd=self._indexes_fd,
                dst_dir_fd=self._indexes_fd,
            )
            os.fsync(self._indexes_fd)
            self._validate_indexes_directory()
        except OSError as exc:
            raise EvidenceError("Cannot write deterministic session index") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=self._indexes_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return self.session.project.indexes_dir / name

    def _rebuild_index_unlocked(self) -> Path:
        """Build one lock-consistent snapshot and reconcile its manifest."""
        self._ensure_open()
        manifest = self._manifest()
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
            byte_count, content_hash, event_count, malformed, sequences = self._file_snapshot(
                relative, parts
            )
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
                if entry.get("size") != byte_count or entry.get("sha256") != content_hash:
                    raise EvidenceError(
                        f"Registered artifact metadata no longer matches: {relative}"
                    )
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
            raise EvidenceError(f"Registered artifact disappeared: {relative}")
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
        return self._write_index(index)

    def rebuild_index(self) -> Path:
        with self._evidence_lock():
            return self._rebuild_index_unlocked()

    def _flush_unlocked(self) -> None:
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

    def flush(self) -> None:
        with self._evidence_lock():
            self._flush_unlocked()

    def finalize(self, status: str = "complete") -> Path:
        """Flush, build the index, then finalize the associated session."""
        with self._evidence_lock():
            self._flush_unlocked()
            index_path = self._rebuild_index_unlocked()
            try:
                self.session._close_from_evidence(status)
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
