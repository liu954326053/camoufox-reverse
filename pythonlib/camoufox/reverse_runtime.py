"""Owned Camoufox runtime for project-scoped reverse captures."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .async_api import AsyncCamoufox
from .reverse_evidence import EvidenceError, EvidenceStore
from .reverse_launch import reverse_launch_options
from .reverse_project import ReverseSession


class AsyncReverseBrowser:
    """Own a Camoufox process and its raw evidence session."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        proxy: str | None = None,
        browser_version: str | None = None,
        trace_profile: str = "overview",
        enable_trace: bool = True,
        **launch_kwargs: Any,
    ):
        self.project_dir = Path(project_dir)
        self.proxy = proxy
        self.browser_version = browser_version
        self.trace_profile = trace_profile
        self.enable_trace = enable_trace
        self.launch_kwargs = launch_kwargs
        self.browser = None
        self.context = None
        self.page = None
        self.session: ReverseSession | None = None
        self.store: EvidenceStore | None = None
        self._cm = None
        self._cm_entered = False
        self._closed = False
        self._request_tasks: set[asyncio.Task[Any]] = set()
        # Keep the object alive: Python may otherwise recycle its numeric id.
        self._request_ids: dict[int, tuple[Any, str]] = {}
        self._request_meta: dict[str, dict[str, Any]] = {}
        self._dirty_requests: set[str] = set()
        self._metadata_versions: dict[str, int] = {}
        self._capture_errors: list[dict[str, Any]] = []
        self._accepting_tasks = True
        self._writes_closed = False
        self._capabilities: dict[str, Any] = {
            "worker_requests": "Only requests emitted by the browser context are observable; "
            "Firefox service-worker attribution and unreported worker traffic are unavailable.",
            "response_bodies": "Browser-provided bytes, not wire-level compressed payloads; "
            "redirect bodies may be unavailable.",
        }

    async def __aenter__(self) -> "AsyncReverseBrowser":
        try:
            options, session = reverse_launch_options(
                project_dir=self.project_dir,
                proxy=self.proxy,
                browser_version=self.browser_version,
                trace_profile=self.trace_profile,
                enable_trace=self.enable_trace,
                **self.launch_kwargs,
            )
            self.session = session
            self.store = EvidenceStore(session)
            self._cm = AsyncCamoufox(from_options=options)
            self.browser = await self._cm.__aenter__()
            self._cm_entered = True
            self.context = (
                self.browser.contexts[0]
                if getattr(self.browser, "contexts", None)
                else await self.browser.new_context()
            )
            if callable(getattr(self.context, "on", None)):
                self._attach_page(self.context)
                self._capabilities["network_scope"] = "context (including popup requests)"
            else:
                self._capabilities["network_scope"] = "page fallback; popup/worker coverage unavailable"
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            if not callable(getattr(self.context, "on", None)):
                for page in self.context.pages:
                    self._attach_page(page)
            return self
        except BaseException as error:
            if self._cm_entered and self._cm is not None:
                try:
                    await self._cm.__aexit__(type(error), error, error.__traceback__)
                except Exception as cleanup_error:
                    self._record_error("browser_cleanup", cleanup_error)
                finally:
                    self._cm_entered = False
            await self._mark_incomplete(error)
            raise

    def _attach_page(self, page: Any) -> None:
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _spawn(self, awaitable: Any) -> None:
        if not self._accepting_tasks:
            awaitable.close()
            self._record_error("late_capture", RuntimeError("capture arrived after shutdown"))
            return
        task = asyncio.create_task(awaitable)
        self._request_tasks.add(task)
        task.add_done_callback(self._task_done)

    def _record_error(self, stage: str, error: BaseException, **details: Any) -> None:
        self._capture_errors.append({"stage": stage, "error": str(error),
                                     "type": type(error).__name__, **details})

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        if task not in self._request_tasks:
            return
        self._request_tasks.discard(task)
        if task.cancelled():
            self._record_error("capture_task", asyncio.CancelledError("capture task cancelled"))
        elif (error := task.exception()) is not None:
            self._record_error("capture_task", error)

    def _request_id(self, request: Any) -> str:
        identity = id(request)
        existing = self._request_ids.get(identity)
        if existing is not None and existing[0] is request:
            return existing[1]
        request_id = uuid.uuid4().hex
        self._request_ids[identity] = (request, request_id)
        return request_id

    def _write_json(self, path: str, value: Any, kind: str) -> Path:
        return self.write_artifact(
            path, json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8"), kind
        )

    def _persist_metadata(self) -> None:
        for request_id in tuple(self._dirty_requests):
            version = self._metadata_versions.get(request_id, 0)
            suffix = "" if version == 0 else f"-{version:06d}"
            try:
                self._write_json(f"raw/network/{request_id}/metadata{suffix}.json",
                                 self._request_meta[request_id], "network-metadata")
            except Exception as error:
                self._record_error("network_metadata", error, request_id=request_id)
            finally:
                self._metadata_versions[request_id] = version + 1
                self._dirty_requests.discard(request_id)

    def _on_request(self, request: Any) -> None:
        if not self._accepting_tasks:
            return
        request_id = self._request_id(request)
        if request_id in self._request_meta:
            return
        meta = {
            "id": request_id,
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "request_headers": [],
        }
        self._request_meta[request_id] = meta
        self._dirty_requests.add(request_id)
        for relation in ("redirected_from", "redirected_to"):
            related = getattr(request, relation, None)
            if related is not None:
                meta[relation] = self._request_id(related)
        if "redirected_from" in meta:
            previous = self._request_meta.get(meta["redirected_from"])
            if previous is not None:
                previous["redirected_to"] = request_id
                self._dirty_requests.add(previous["id"])
        try:
            self._write_json(f"raw/network/{request_id}/request.json", meta, "request-metadata")
        except Exception as error:
            self._record_error("request_metadata", error, request_id=request_id)
        try:
            body = request.post_data_buffer
            if body is not None:
                self.write_artifact(f"raw/network/{request_id}/request.body", body, "request-body")
        except Exception as error:
            meta["request_body_error"] = str(error)
            self._record_error("request_body", error, request_id=request_id)
        if request.resource_type == "script":
            meta["script"] = True
        self._spawn(self._capture_request_headers(request, meta))

    async def _headers(self, source: Any, meta: dict[str, Any], key: str) -> None:
        try:
            # The dictionary property omits security headers and folds duplicates.
            meta[key] = await source.headers_array()
        except Exception as error:
            meta[key + "_error"] = str(error)
            self._record_error(key, error, request_id=meta["id"])
        finally:
            self._dirty_requests.add(meta["id"])

    async def _capture_request_headers(self, request: Any, meta: dict[str, Any]) -> None:
        await self._headers(request, meta, "request_headers")

    def _on_response(self, response: Any) -> None:
        self._spawn(self._capture_response(response))

    def _on_request_failed(self, request: Any) -> None:
        if not self._accepting_tasks:
            return
        self._on_request(request)
        request_id = self._request_id(request)
        self._request_meta[request_id]["failure"] = request.failure
        self._dirty_requests.add(request_id)

    async def _capture_response(self, response: Any) -> None:
        request = response.request
        self._on_request(request)
        request_id = self._request_id(request)
        meta = self._request_meta[request_id]
        meta["status"] = response.status
        await self._headers(response, meta, "response_headers")
        try:
            lengths = [header["value"].strip() for header in meta.get("response_headers", [])
                       if header["name"].lower() == "content-length"]
            if request.method.upper() == "HEAD" or response.status in {204, 304}:
                body = b""
                meta["body_availability"] = "empty_by_http_semantics"
            elif lengths and all(value == "0" for value in lengths):
                body = b""
                meta["body_availability"] = "empty_by_header"
            else:
                body = await response.body()
                meta["body_availability"] = "captured"
            self.write_artifact(f"raw/network/{request_id}/response.body", body, "response-body")
            if meta.get("script"):
                self.write_artifact(f"raw/scripts/{request_id}.js", body, "script")
        except Exception as error:
            meta["body_availability"] = "unavailable"
            meta["response_body_error"] = str(error)
            self._record_error("response_body", error, request_id=request_id)
        finally:
            self._dirty_requests.add(request_id)

    async def _cancel_capture_tasks(self) -> None:
        self._accepting_tasks = False
        pending = tuple(self._request_tasks)
        if not pending:
            return
        for task in pending:
            task.cancel()
        done, still_pending = await asyncio.wait(pending, timeout=0.1)
        for task in done:
            self._task_done(task)
        if still_pending:
            self._record_error("cancellation_timeout", TimeoutError("capture ignored cancellation"),
                               pending=len(still_pending))

    async def drain(self, timeout: float = 30.0) -> None:
        deadline = asyncio.get_running_loop().time() + (timeout if self._accepting_tasks else 0)
        while self._request_tasks:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self._record_error("drain_timeout", TimeoutError("capture drain timeout"),
                                   pending=len(self._request_tasks))
                # Freeze new work before cancellation; callbacks may spawn more tasks.
                await self._cancel_capture_tasks()
                break
            done, _ = await asyncio.wait(tuple(self._request_tasks), timeout=remaining,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                self._task_done(task)
        self._persist_metadata()

    def write_artifact(self, relative_path: str | Path, data: bytes, kind: str) -> Path:
        if self.session is None or self.store is None:
            raise RuntimeError("reverse browser is not started")
        if getattr(self, "_writes_closed", False):
            raise RuntimeError("reverse capture is closed")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("artifact data must be bytes-like")
        path = Path(relative_path)
        if path.is_absolute():
            try:
                path = path.resolve(strict=False).relative_to(self.session.path.resolve(strict=True))
            except ValueError as exc:
                raise ValueError("artifact path escapes session") from exc
        if not path.parts or path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact path must stay inside session")
        if path.parts[0] not in {"raw", "trace"}:
            raise ValueError("artifact path must start with raw or trace")
        normalized = path.as_posix()
        target = self.session.path / path
        if target.exists() or target.is_symlink():
            raise ValueError("artifact already exists")
        self.store.append_bytes(normalized, data)
        digest = hashlib.sha256(data).hexdigest()
        self.store.register_artifact(normalized, digest, len(data), kind)
        return target

    async def snapshot(self) -> dict[str, Any]:
        if self.context is None:
            raise RuntimeError("reverse browser is not started")
        try:
            return await asyncio.wait_for(self._snapshot(), timeout=30.0)
        except Exception as error:
            self._record_error("snapshot", error)
            raise

    async def _snapshot(self) -> dict[str, Any]:
        state = dict(await self.context.storage_state())
        frames = []
        for page_index, page in enumerate(tuple(self.context.pages)):
            accessible_frames = getattr(page, "frames", None)
            if accessible_frames is None:
                frames.append({"page": page_index, "status": "inaccessible",
                               "error": "frame enumeration unavailable"})
                self._record_error("session_storage", RuntimeError("frame enumeration unavailable"))
                continue
            for frame_index, frame in enumerate(tuple(accessible_frames)):
                entry = {"page": page_index, "frame": frame_index, "url": frame.url}
                try:
                    entry["storage"] = await asyncio.wait_for(frame.evaluate(
                        "() => Object.fromEntries(Object.entries(sessionStorage))"
                    ), timeout=5.0)
                    entry["status"] = "captured"
                except Exception as error:
                    entry.update(status="inaccessible", error=str(error))
                    self._record_error("session_storage", error, page=page_index, frame=frame_index)
                frames.append(entry)
        state["sessionStorage"] = frames
        path = self._write_json(f"raw/state/{uuid.uuid4().hex}/storage-state.json", state, "storage-state")
        return {"path": str(path), "cookies": len(state.get("cookies", []))}

    def _native_io(self, path: Path, data: bytes | None = None) -> bytes:
        """Read small producer files or write controls without following symlinks."""
        parts = path.relative_to(self.session.path).parts
        if not parts or parts[0] != "trace" or ".." in parts:
            raise ValueError("native control path must stay inside session trace directory")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(self.session.path, directory_flags)
        try:
            for part in parts[:-1]:
                child = os.open(part, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            flags = os.O_RDONLY if data is None else os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            descriptor = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=parent)
            with os.fdopen(descriptor, "rb" if data is None else "wb") as file:
                if data is None:
                    payload = file.read(65537)
                    if len(payload) > 65536:
                        raise ValueError("native status file exceeds 64 KiB")
                    return payload
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
                return data
        finally:
            os.close(parent)

    async def _stop_native_trace(self, timeout: float = 3.0) -> None:
        if not self.enable_trace or self.session is None:
            return
        root = self.session.trace_dir
        deadline = time.monotonic() + min(timeout, 3.0)
        controls: set[Path] = set()
        statuses: set[Path] = set()
        traces: set[Path] = set()
        desired: set[Path] = set()
        status_bytes: dict[Path, bytes] = {}
        acknowledged: set[Path] = set()
        quiet_since = None
        previous = None
        capture_root = f"raw/native-status/{uuid.uuid4().hex}"

        def command(path: Path) -> None:
            try:
                self._native_io(path, b"off\n")
            except Exception as error:
                self._record_error("native_stop_command", error, path=str(path.relative_to(root)))

        command(root / "desired.state")
        desired.add(root / "desired.state")
        while True:
            # Poll with a deadline and sleep, never a busy wait or an unbounded scan.
            for directory, dirs, files in os.walk(root, followlinks=False):
                if time.monotonic() >= deadline:
                    break
                base = Path(directory)
                dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
                for name in files:
                    if time.monotonic() >= deadline:
                        break
                    path = base / name
                    if base.name == "control" and name.startswith("control-") and name.endswith(".cmd"):
                        status = base / ("status-" + name[len("control-"):-4] + ".state")
                        statuses.add(status)
                        if path not in controls:
                            desired_path = base.parent / "desired.state"
                            if desired_path not in desired:
                                command(desired_path)
                                desired.add(desired_path)
                            command(path)
                            controls.add(path)
                    elif base.name == "control" and name.startswith("status-") and name.endswith(".state"):
                        statuses.add(path)
                    elif name.endswith(".jsonl"):
                        traces.add(path)
            for path in statuses:
                try:
                    payload = self._native_io(path)
                    status_bytes[path] = payload
                    if payload.split(maxsplit=1)[:1] == [b"off"]:
                        acknowledged.add(path)
                    else:
                        acknowledged.discard(path)
                except Exception:
                    acknowledged.discard(path)
            signature = (frozenset(controls), frozenset(statuses), frozenset(traces))
            now = time.monotonic()
            if signature != previous or statuses != acknowledged:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            if quiet_since is not None and now - quiet_since >= 0.2:
                break
            if now >= deadline:
                break
            previous = signature
            await asyncio.sleep(min(0.05, deadline - now))

        if not statuses:
            self._record_error("native_producers", RuntimeError("no native producer acknowledgements; event loss unknown"))
        if quiet_since is None or time.monotonic() - quiet_since < 0.2:
            self._record_error("native_stop_quiescence", TimeoutError("native producers did not become quiescent"))
        for path in statuses - acknowledged:
            self._record_error("native_stop_ack", TimeoutError("missing off acknowledgement"),
                               path=str(path.relative_to(root)))
        for path, payload in status_bytes.items():
            try:
                self.write_artifact(f"{capture_root}/{path.relative_to(root).as_posix()}",
                                    payload, "native-status")
                text = payload.decode("utf-8")
                counters = dict(token.split("=", 1) for token in text.split() if "=" in token)
                if "dropped" not in counters:
                    raise ValueError("native status has no dropped counter; event loss unknown")
                if int(counters["dropped"]) != 0 or "write_error" in text:
                    raise ValueError("native producer reports lost events or a write error")
            except Exception as error:
                self._record_error("native_status", error, path=str(path.relative_to(root)))
        for path in traces:
            sidecar = Path(str(path) + ".meta.json")
            try:
                payload = self._native_io(sidecar)
                self.write_artifact(f"{capture_root}/{sidecar.relative_to(root).as_posix()}",
                                    payload, "native-metadata")
                meta = json.loads(payload)
                if meta.get("state") != "off" or type(meta.get("dropped")) is not int:
                    raise ValueError("native sidecar incomplete; event loss unknown")
                if meta["dropped"] != 0 or meta.get("detail"):
                    raise ValueError("native sidecar reports lost events or a write error")
            except Exception as error:
                self._record_error("native_sidecar", error, path=str(sidecar.relative_to(root)))

    async def _mark_incomplete(self, error: BaseException | None = None) -> Path | None:
        if self.session is None:
            return
        index_path = None
        if self.store is not None:
            for stage, operation in (("flush", self.store.flush),
                                     ("index", self.store.rebuild_index)):
                try:
                    result = operation()
                    if stage == "index":
                        index_path = result
                except Exception as finalization_error:
                    self._record_error(stage, finalization_error)
        try:
            self.session.mark_incomplete(error)
        except Exception as finalization_error:
            self._record_error("mark_incomplete", finalization_error)
            try:
                # Emergency lifecycle-only transition when the index cannot be built.
                self.session._close_manifest("incomplete")
            except Exception as emergency_error:
                self._record_error("emergency_status", emergency_error)
                raise EvidenceError("Capture incomplete; session status could not be persisted") from emergency_error
        return index_path

    async def close(self, *, incomplete: bool = False) -> dict[str, Any]:
        if self._closed:
            return {
                "status": "already_closed",
                "session_id": self.session.session_id if self.session else None,
            }
        self._closed = True
        index_path = None
        cancelled = None
        try:
            await self.drain()
            if self.context is not None:
                await self.snapshot()
        except asyncio.CancelledError as error:
            cancelled = error
            self._record_error("close_cancelled", error)
        except Exception as error:
            self._record_error("close_capture", error)
        finally:
            try:
                await self._stop_native_trace()
            except asyncio.CancelledError as error:
                cancelled = error
                self._record_error("native_stop", error)
            except Exception as error:
                self._record_error("native_stop", error)
            finally:
                if self._cm_entered and self._cm is not None:
                    try:
                        await self._cm.__aexit__(None, None, None)
                    except asyncio.CancelledError as error:
                        cancelled = error
                        self._record_error("browser_cleanup", error)
                    except Exception as error:
                        self._record_error("browser_cleanup", error)
                    finally:
                        self._cm_entered = False
        try:
            # Browser shutdown can emit failed requests and complete outstanding bodies.
            await self.drain()
        except asyncio.CancelledError as error:
            cancelled = error
            self._record_error("shutdown_drain", error)
            await self._cancel_capture_tasks()
        except Exception as error:
            self._record_error("shutdown_drain", error)
        self._accepting_tasks = False
        for request_id, meta in self._request_meta.items():
            if "status" not in meta and "failure" not in meta:
                meta["capture_gap"] = "request has no response or failure event at shutdown"
                self._dirty_requests.add(request_id)
                self._record_error("unfinished_request", RuntimeError(meta["capture_gap"]),
                                   request_id=request_id)
        self._persist_metadata()
        capture = {"capabilities": self._capabilities, "errors": self._capture_errors}
        status_path = None
        try:
            status_path = self._write_json("raw/capture-status.json", capture, "capture-status")
        except Exception as error:
            self._record_error("capture_status", error)
        self._writes_closed = True
        incomplete = incomplete or bool(self._capture_errors)
        try:
            if self.store is not None:
                if incomplete:
                    index_path = await self._mark_incomplete()
                else:
                    index_path = self.store.finalize()
            elif self.session is not None:
                if incomplete:
                    await self._mark_incomplete()
                else:
                    self.session.close()
        except Exception as error:
            self._record_error("finalize", error)
            incomplete = True
            index_path = await self._mark_incomplete(error)
        if cancelled is not None:
            raise cancelled
        return {
            "status": "incomplete" if incomplete else "closed",
            "session_id": self.session.session_id if self.session else None,
            "session_dir": str(self.session.path) if self.session else None,
            "artifacts": {**({"index": str(index_path)} if index_path else {}),
                          **({"capture": str(status_path)} if status_path else {})},
            "capture": capture,
        }

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        await self.close(incomplete=exc_type is not None)
