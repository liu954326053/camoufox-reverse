"""Owned Camoufox runtime for project-scoped reverse captures."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
        self._request_ids: dict[int, str] = {}
        self._request_meta: dict[str, dict[str, Any]] = {}

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
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self._attach_page(self.page)
            return self
        except Exception as error:
            if self._cm_entered and self._cm is not None:
                try:
                    await self._cm.__aexit__(type(error), error, error.__traceback__)
                except Exception:
                    pass
                finally:
                    self._cm_entered = False
            await self._mark_incomplete(error)
            raise

    def _attach_page(self, page: Any) -> None:
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _spawn(self, awaitable: Any) -> None:
        task = asyncio.create_task(awaitable)
        self._request_tasks.add(task)
        task.add_done_callback(self._request_tasks.discard)

    def _on_request(self, request: Any) -> None:
        request_id = uuid.uuid4().hex
        self._request_ids[id(request)] = request_id
        meta = {
            "id": request_id,
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "request_headers": dict(request.headers),
        }
        self._request_meta[request_id] = meta
        try:
            body = getattr(request, "post_data_buffer", None)
            if body is None:
                post_data = request.post_data
                body = post_data.encode("utf-8") if post_data is not None else None
            if body:
                self.write_artifact(f"raw/network/{request_id}/request.body", body, "request-body")
        except Exception:
            meta["request_body_error"] = True
        if request.resource_type == "script":
            meta["script"] = True

    def _on_response(self, response: Any) -> None:
        self._spawn(self._capture_response(response))

    def _on_request_failed(self, request: Any) -> None:
        request_id = self._request_ids.get(id(request))
        if request_id:
            self._request_meta[request_id]["failure"] = request.failure

    async def _capture_response(self, response: Any) -> None:
        request = response.request
        request_id = self._request_ids.get(id(request))
        if not request_id:
            return
        meta = self._request_meta[request_id]
        meta.update(
            {
                "status": response.status,
                "response_headers": dict(response.headers),
            }
        )
        try:
            body = await response.body()
            self.write_artifact(f"raw/network/{request_id}/response.body", body, "response-body")
            if meta.get("script"):
                self.write_artifact(f"raw/scripts/{request_id}.js", body, "script")
        except Exception:
            meta["response_body_error"] = True
        self.write_artifact(
            f"raw/network/{request_id}/metadata.json",
            json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            "network-metadata",
        )

    async def drain(self) -> None:
        if self._request_tasks:
            await asyncio.gather(*tuple(self._request_tasks), return_exceptions=True)

    def write_artifact(self, relative_path: str | Path, data: bytes, kind: str) -> Path:
        if self.session is None or self.store is None:
            raise RuntimeError("reverse browser is not started")
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
        if target.exists() or target.is_symlink() or normalized in getattr(self.store, "files", {}):
            raise ValueError("artifact already exists")
        self.store.append_bytes(normalized, data)
        digest = hashlib.sha256(data).hexdigest()
        self.store.register_artifact(normalized, digest, len(data), kind)
        return target

    async def snapshot(self) -> dict[str, Any]:
        if self.context is None:
            raise RuntimeError("reverse browser is not started")
        state = await self.context.storage_state()
        path = self.write_artifact(
            "raw/state/storage-state.json",
            json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"),
            "storage-state",
        )
        return {"path": str(path), "cookies": len(state.get("cookies", []))}

    async def _mark_incomplete(self, error: BaseException | None = None) -> None:
        if self.session is None:
            return
        try:
            if self.store is not None:
                await self.drain()
                self.store.flush()
                self.store.rebuild_index()
            self.session.mark_incomplete(error)
        except Exception:
            pass

    async def close(self, *, incomplete: bool = False) -> dict[str, Any]:
        if self._closed:
            return {
                "status": "already_closed",
                "session_id": self.session.session_id if self.session else None,
            }
        self._closed = True
        index_path = None
        try:
            await self.drain()
            if self.context is not None:
                try:
                    await self.snapshot()
                except Exception:
                    pass
            if self._cm_entered and self._cm is not None:
                try:
                    await self._cm.__aexit__(None, None, None)
                finally:
                    self._cm_entered = False
            if self.store is not None:
                if incomplete:
                    self.store.flush()
                    index_path = self.store.rebuild_index()
                    self.session.mark_incomplete()
                else:
                    index_path = self.store.finalize()
            elif self.session is not None:
                self.session.mark_incomplete() if incomplete else self.session.close()
            status = "incomplete" if incomplete else "closed"
            return {
                "status": status,
                "session_id": self.session.session_id if self.session else None,
                "session_dir": str(self.session.path) if self.session else None,
                "artifacts": {"index": str(index_path)} if index_path else {},
            }
        except Exception as error:
            await self._mark_incomplete(error)
            raise

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        await self.close(incomplete=exc_type is not None)
