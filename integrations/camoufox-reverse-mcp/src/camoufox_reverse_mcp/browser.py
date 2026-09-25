from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os as _os
import platform
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from playwright.async_api import Page, BrowserContext

MAX_LOG_SIZE = 2000
MAX_BODY_SIZE = 200_000


def detect_host_os() -> str:
    """Return the Camoufox os identifier matching the current host."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "windows"


def detect_system_locale() -> str:
    """Best-effort detection of the host's locale (e.g. 'zh-CN')."""
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = _os.environ.get(var, "")
        if val and val not in ("C", "POSIX"):
            locale = val.split(".")[0].replace("_", "-")
            if locale not in ("C", "POSIX"):
                return locale
    return "en-US"


class BrowserManager:
    """Manages the Camoufox browser lifecycle, contexts, and pages."""

    default_config: dict[str, Any] = {}

    def __init__(self, runtime_factory=None) -> None:
        self.browser = None
        self.runtime = None
        self.session = None
        self.store = None
        self._runtime_factory = runtime_factory
        self._last_close_result: dict[str, Any] | None = None
        self.contexts: dict[str, BrowserContext] = {}
        self.pages: dict[str, Page] = {}
        self.active_page_name: str | None = None
        self._cm = None  # AsyncCamoufox context manager
        self._console_logs: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._network_requests: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._request_id_counter = 0
        self._capturing = False
        self._capture_pattern: str = "**/*"
        self._capture_body = False
        # 第十二阶段：引擎层 initiator 栈（Juggler content 进程 Components.stack
        # 捕获，经 driver initializer 透出）。计数自描述：captured=拿到栈，
        # unavailable=该请求通道打开时栈上无页面 JS（浏览器自身发起/导航/worker 等）。
        self._engine_initiator_stats = {"captured": 0, "unavailable": 0}
        # 第十三阶段：引擎层 body 捕获（juggler nsIUploadChannel / ResponseStorage
        # 通道，零页面污染）。计数自描述：request_captured=拿到请求体，
        # response_captured/response_unavailable=响应体拿到/拿不到（evicted、
        # redirect、SW 合成等）。
        self._engine_body_stats = {
            "request_captured": 0,
            "response_captured": 0,
            "response_unavailable": 0,
        }
        self._init_scripts: list[str] = []
        self._persistent_scripts: list[dict] = []
        self._persistent_traces: dict[str, list] = {}
        self._nav_responses: list[dict] = []  # 最近一次 navigate 记录到的响应链路
        self._route_handlers: dict[str, Any] = {}  # 已注册的 route handler 映射
        from .network_evidence import NetworkEvidence
        self.network_evidence = NetworkEvidence(self)
        self._body_tasks: set[asyncio.Task] = set()

    async def launch(self, config: dict | None = None, *, project_dir=None,
                     capture_profile: str = "raw") -> dict:
        """Launch the Camoufox browser with the given or default config."""
        if self.browser is not None:
            pages_info = {}
            for name, p in self.pages.items():
                try:
                    pages_info[name] = p.url
                except Exception:
                    pages_info[name] = "unknown"
            return {
                "status": "already_running",
                "active_page": self.active_page_name,
                "pages": pages_info,
                "contexts": list(self.contexts.keys()),
                "capturing": self._capturing,
            }

        cfg = {**self.default_config, **(config or {})}

        project_dir = project_dir or cfg.pop("project_dir", None)
        if not project_dir:
            raise ValueError("project_dir is required; call launch_browser first")
        if capture_profile != "raw":
            raise ValueError("capture_profile must be 'raw'")

        runtime_factory = self._runtime_factory or _load_runtime_factory()
        runtime_kwargs: dict[str, Any] = {}

        proxy = cfg.get("proxy")
        if isinstance(proxy, dict):
            proxy = proxy.get("server")
        if proxy:
            runtime_kwargs["proxy"] = proxy

        os_type = cfg.get("os", "auto")
        if os_type == "auto":
            os_type = detect_host_os()
        locale = cfg.get("locale", "auto")
        if locale == "auto":
            locale = detect_system_locale()

        runtime_kwargs.update({
            "headless": cfg.get("headless", False),
            "os": os_type,
            "locale": locale,
            "humanize": bool(cfg.get("humanize")),
            "geoip": bool(cfg.get("geoip")),
            "block_images": bool(cfg.get("block_images")),
            "block_webrtc": bool(cfg.get("block_webrtc")),
            "enable_trace": cfg.get("enable_trace", True),
            "browser_version": cfg.get("browser_version"),
            "trace_profile": cfg.get("trace_profile", "overview"),
            "main_world_eval": True,
        })
        runtime_kwargs = {k: v for k, v in runtime_kwargs.items() if v is not None}

        self.runtime = runtime_factory(project_dir=project_dir, **runtime_kwargs)
        try:
            entered = await self.runtime.__aenter__()
        except Exception:
            self.runtime = None
            raise

        runtime_view = entered if hasattr(entered, "session") else self.runtime
        self.browser = getattr(entered, "browser", None) or getattr(runtime_view, "browser", None) or entered
        self.session = getattr(runtime_view, "session", None)
        self.store = getattr(runtime_view, "store", None)
        if self.session is not None:
            from .property_trace import configure_session
            configure_session(self.session.trace_dir)
        ctx = getattr(runtime_view, "context", None)
        if ctx is None:
            ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.contexts["default"] = ctx

        if any(s["name"].split(":")[-1] in {"xhr", "fetch"} for s in self._persistent_scripts):
            await self.network_evidence.install(ctx)
        for script_info in self._persistent_scripts:
            from .network_evidence import init_script
            await ctx.add_init_script(script=init_script(script_info["content"]))

        page = getattr(runtime_view, "page", None) or (ctx.pages[0] if ctx.pages else await ctx.new_page())
        self._attach_listeners(page)
        self.pages["default"] = page
        self.active_page_name = "default"

        return {
            "status": "launched",
            "headless": runtime_kwargs.get("headless", False),
            "os": runtime_kwargs.get("os", "auto"),
            "locale": runtime_kwargs.get("locale", "auto"),
            "proxy": runtime_kwargs.get("proxy"),  # 回显生效代理（None=直连）
            "pages": list(self.pages.keys()),
            **self.session_metadata(),
        }

    async def _ensure_browser(self) -> None:
        """Require an explicit launch_browser call."""
        if self.browser is None:
            raise RuntimeError("Browser is not running. Call launch_browser(project_dir=...) first.")

    def session_metadata(self) -> dict[str, Any]:
        if self.session is None:
            return {}
        session_path = Path(self.session.path).resolve()
        return {
            "session_id": self.session.session_id,
            "session_dir": str(session_path),
            "artifacts": {
                "manifest": str(Path(self.session.manifest_path).resolve()),
                "trace_dir": str(Path(self.session.trace_dir).resolve()),
            },
        }

    def _session_relative(self, path: str | os.PathLike[str] | None, default: str) -> str:
        if self.session is None:
            raise RuntimeError("No active reverse session")
        root = Path(self.session.path).resolve()
        candidate = Path(path) if path else Path(default)
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("artifact path contains traversal")
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=False)
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError("artifact path must stay inside the active session") from exc
        else:
            relative = candidate
        if not relative.parts or relative.parts[0] not in {"raw", "trace"}:
            relative = Path(default).parent / relative.name
        return relative.as_posix()

    def write_artifact(self, path: str | os.PathLike[str] | None, data: bytes,
                       kind: str, *, default: str) -> Path:
        if self.runtime is None:
            raise RuntimeError("No active reverse session")
        relative = self._session_relative(path, default)
        return Path(self.runtime.write_artifact(relative, bytes(data), kind))

    def manifest_snapshot(self) -> dict[str, Any]:
        if self.session is None:
            return {}
        return self.session.manifest_snapshot()

    async def add_persistent_script(self, name: str, content: str) -> None:
        """Register a script that persists across all navigations via context-level injection."""
        if name.split(":")[-1] in {"xhr", "fetch"}:
            from .network_evidence import network_hook_script
            if not content.startswith("mw:"):
                content = network_hook_script(name.split(":")[-1], content)
            for ctx in self.contexts.values():
                await self.network_evidence.install(ctx)
        for s in self._persistent_scripts:
            if s["name"] == name:
                s["content"] = content
                break
        else:
            self._persistent_scripts.append({"name": name, "content": content})
        for ctx in self.contexts.values():
            from .network_evidence import init_script
            await ctx.add_init_script(script=init_script(content))

    def remove_persistent_script(self, name: str) -> bool:
        """Remove a persistent script by name. Returns True if found."""
        before = len(self._persistent_scripts)
        self._persistent_scripts = [s for s in self._persistent_scripts if s["name"] != name]
        return len(self._persistent_scripts) < before

    def _attach_listeners(self, page: Page) -> None:
        """Attach console, network, and trace-collection listeners to a page."""
        page.on("console", self._on_console)
        page.on("request", self._on_request)
        page.on("response", self._on_response_async)
        page.on("response", self._on_response_for_nav)
        # 第十二阶段：监听 requestfailed，让中止/失败请求自描述终态
        page.on("requestfailed", self._on_request_failed)

    def _on_console(self, msg) -> None:
        text = msg.text
        if text and text.startswith("__MCP_TRACE__:"):
            try:
                import json
                payload = json.loads(text[len("__MCP_TRACE__:"):])
                path = payload.pop("__path__", "unknown")
                self._persistent_traces.setdefault(path, []).append(payload)
            except Exception:
                pass
            return

        self._console_logs.append({
            "level": msg.type,
            "text": text,
            "timestamp": int(time.time() * 1000),
            "location": str(msg.location) if hasattr(msg, "location") else None,
        })

    def _on_request(self, req) -> None:
        if not self._capturing:
            return
        import fnmatch
        if not fnmatch.fnmatch(req.url, self._capture_pattern):
            return
        self._request_id_counter += 1
        try:
            post_data = req.post_data
        except UnicodeDecodeError:
            post_data = None  # Lossless bytes are stored separately as base64.
        # 第十三阶段：引擎层请求体。链路：juggler NetworkObserver
        # readRequestPostData（http-on-modify-request 时读 nsIUploadChannel，
        # 页面世界不可见）→ driver postDataBuffer → req.post_data_buffer。
        # base64 无损存储；超 MAX_BODY_SIZE 截断并自描述。
        # juggler 侧上限 10MB，超限请求体在引擎层即缺席（记 None，不伪造）。
        engine_request_body = None
        engine_request_body_size = None
        engine_request_body_truncated = False
        try:
            body_bytes = req.post_data_buffer
        except Exception:
            body_bytes = None
        if isinstance(body_bytes, (bytes, bytearray)) and body_bytes:
            body_bytes = bytes(body_bytes)
            engine_request_body_size = len(body_bytes)
            engine_request_body_truncated = engine_request_body_size > MAX_BODY_SIZE
            engine_request_body = base64.b64encode(
                body_bytes[:MAX_BODY_SIZE]).decode("ascii")
            self._engine_body_stats["request_captured"] += 1
        # 引擎层 initiator 栈：driver initializer 透出（未打 driver 补丁或
        # 通道打开时栈上无页面 JS 时为 None）。与页面世界 hook 的 initiator
        # 并存，互不覆盖——引擎栈是零容忍目标的唯一零污染通道。
        engine_initiator_stack = None
        impl = getattr(req, "_impl_obj", None)
        initializer = getattr(impl, "_initializer", None)
        if isinstance(initializer, dict):
            candidate = initializer.get("engineInitiatorStack")
            if isinstance(candidate, list) and candidate:
                engine_initiator_stack = candidate
        if engine_initiator_stack:
            self._engine_initiator_stats["captured"] += 1
        else:
            self._engine_initiator_stats["unavailable"] += 1
        entry = {
            "id": self._request_id_counter,
            "url": req.url,
            "method": req.method,
            "resource_type": req.resource_type,
            "request_headers": dict(req.headers),
            "request_post_data": post_data,
            # 引擎层请求体（零污染通道）：base64 文本，None=无请求体或引擎层缺席。
            "engine_request_body": engine_request_body,
            "engine_request_body_size": engine_request_body_size,
            "engine_request_body_truncated": engine_request_body_truncated,
            "timestamp": int(time.time() * 1000),
            "status": None,
            "response_headers": None,
            "response_body": None,
            # 引擎层响应体（juggler ResponseStorage → getResponseBody）：
            # base64 文本；仅 network_capture(capture_body=True) 时填充。
            "engine_response_body": None,
            "engine_response_body_size": None,
            "engine_response_body_truncated": False,
            "duration": None,
            "engine_initiator_stack": engine_initiator_stack,
            # 第十二阶段：终态自描述字段。pending=在途，answered=已见响应，
            # failed=已见 requestfailed；拿不到原因时记 "unknown"，不编造。
            "terminal_state": "pending",
            "failure_reason": None,
            "pending_at_close": False,
        }
        self._network_requests.append(entry)
        self.network_evidence.record_request(req, entry)

    def _on_request_failed(self, req) -> None:
        """第十二阶段：登记 Playwright requestfailed 事件的终态与失败原因。"""
        entry = self.network_evidence.requests.get(req)
        if entry is None:
            return  # 未捕获的请求（捕获未开启或被 pattern 过滤），不入账
        try:
            failure = req.failure
        except Exception:
            failure = None
        entry["terminal_state"] = "failed"
        entry["failure_reason"] = failure if failure else "unknown"
        entry["duration"] = int(time.time() * 1000) - entry["timestamp"]
        self.network_evidence.write("requests", entry["evidence_id"], entry)

    def finalize_pending_requests(self) -> int:
        """第十二阶段：捕获停止/浏览器关闭时，把仍在途的请求统一登记为
        pending-at-close。只标记、不改 terminal_state，不伪造失败原因。
        返回被标记的在途请求数。"""
        marked = 0
        for entry in self._network_requests:
            if entry.get("terminal_state") != "pending":
                continue
            entry["pending_at_close"] = True
            marked += 1
            evidence_id = entry.get("evidence_id")
            if evidence_id:
                self.network_evidence.write("requests", evidence_id, entry)
        return marked

    def _on_response_async(self, resp) -> None:
        """Handle response events, optionally capturing body asynchronously."""
        entry = self.network_evidence.requests.get(resp.request)
        if entry is None:
            return
        entry["status"] = resp.status
        entry["response_headers"] = dict(resp.headers)
        entry["duration"] = int(time.time() * 1000) - entry["timestamp"]
        # 第十二阶段：见到响应即终态 answered
        entry["terminal_state"] = "answered"
        entry["pending_at_close"] = False
        self.network_evidence.write("requests", entry["evidence_id"], entry)
        if self._capture_body:
            task = asyncio.create_task(self._fetch_response_body(resp, entry))
            self._body_tasks.add(task)
            task.add_done_callback(self._body_tasks.discard)

    async def _fetch_response_body(self, resp, entry: dict) -> None:
        """Asynchronously fetch and store the response body."""
        try:
            body_bytes = await resp.body()
            # 第十三阶段：引擎层响应体（juggler ResponseStorage 在父进程
            # onStopRequest 时落盘，页面世界零接触）。原始字节 base64 无损
            # 保存，超 MAX_BODY_SIZE 截断并自描述；文本 response_body 维持原逻辑。
            entry["engine_response_body_size"] = len(body_bytes)
            entry["engine_response_body_truncated"] = len(body_bytes) > MAX_BODY_SIZE
            entry["engine_response_body"] = base64.b64encode(
                body_bytes[:MAX_BODY_SIZE]).decode("ascii")
            self._engine_body_stats["response_captured"] += 1
            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = body_bytes.decode("latin-1")
            if len(body_text) > MAX_BODY_SIZE:
                entry["response_body"] = body_text[:MAX_BODY_SIZE]
                entry["response_body_truncated"] = True
                entry["response_body_total_size"] = len(body_text)
            else:
                entry["response_body"] = body_text
        except Exception:
            entry["response_body"] = None
            entry["engine_response_body"] = None
            self._engine_body_stats["response_unavailable"] += 1

    async def persist_before_navigation(self) -> None:
        """Fail closed before discarding a document or clearing its UI buffer."""
        await asyncio.wait_for(self.network_evidence.checkpoint(), timeout=30)
        if self.runtime is not None and callable(getattr(self.runtime, "snapshot", None)):
            await self.runtime.snapshot()
        if self.runtime is not None and callable(getattr(self.runtime, "drain", None)):
            await self.runtime.drain()
        if self._body_tasks:
            await asyncio.wait_for(asyncio.gather(*tuple(self._body_tasks)), timeout=30)
        await asyncio.wait_for(self.network_evidence.checkpoint(), timeout=30)

    def _on_response_for_nav(self, resp) -> None:
        """Record every response during a navigation for final_status resolution."""
        try:
            self._nav_responses.append({
                "url": resp.url,
                "status": resp.status,
                "resource_type": getattr(resp.request, "resource_type", None) if resp.request else None,
                "ts": int(time.time() * 1000),
            })
            # Keep only the last 100
            if len(self._nav_responses) > 100:
                self._nav_responses = self._nav_responses[-100:]
        except Exception:
            pass

    def reset_nav_responses(self) -> None:
        self._nav_responses = []

    async def create_context(self, name: str, cookies: list[dict] | None = None) -> dict:
        """Create a new isolated browser context with optional cookies."""
        await self._ensure_browser()
        ctx = await self.browser.new_context()
        if cookies:
            await ctx.add_cookies(cookies)
        if self.network_evidence.contexts:
            await self.network_evidence.install(ctx)
        for script_info in self._persistent_scripts:
            from .network_evidence import init_script
            await ctx.add_init_script(script=init_script(script_info["content"]))
        self.contexts[name] = ctx
        page = await ctx.new_page()
        self._attach_listeners(page)
        self.pages[name] = page
        self.active_page_name = name
        return {"status": "created", "context": name}

    async def get_active_page(self) -> Page:
        """Get the currently active page, launching the browser if needed."""
        await self._ensure_browser()
        if self.active_page_name and self.active_page_name in self.pages:
            return self.pages[self.active_page_name]
        raise RuntimeError("No active page available. Call launch_browser first.")

    async def close(self) -> dict:
        """Close the browser and clean up all resources."""
        if self.runtime is None:
            result = dict(self._last_close_result or {})
            result["status"] = "already_closed"
            return result or {"status": "already_closed"}
        runtime = self.runtime
        capture_incomplete = False
        try:
            # 第十二阶段：关闭前把仍在途请求登记为 pending-at-close 并落盘
            self.finalize_pending_requests()
            await self.persist_before_navigation()
        except Exception:
            capture_incomplete = True
        capture_incomplete = capture_incomplete or bool(self.network_evidence.gaps)
        try:
            result = await runtime.close(incomplete=capture_incomplete)
        except Exception:
            try:
                await runtime.close(incomplete=True)
            except Exception:
                pass
            raise
        result["mcp_capture_gaps"] = len(self.network_evidence.gaps)
        self._last_close_result = result
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.browser = None
        self.contexts.clear()
        self.pages.clear()
        self.active_page_name = None
        self._cm = None
        self.runtime = None
        self.session = None
        self.store = None
        self._console_logs.clear()
        self._network_requests.clear()
        self._request_id_counter = 0
        self._capturing = False
        self._capture_body = False
        self._init_scripts.clear()
        self._persistent_scripts.clear()
        self._persistent_traces.clear()
        self._nav_responses.clear()
        self._route_handlers.clear()
        from .network_evidence import NetworkEvidence
        self.network_evidence = NetworkEvidence(self)
        self._body_tasks.clear()
        from .property_trace import clear_session
        clear_session()
        return result


def _load_runtime_factory():
    try:
        from camoufox.reverse_runtime import AsyncReverseBrowser
    except ImportError as exc:
        raise RuntimeError(
            "camoufox.reverse_runtime.AsyncReverseBrowser is required for the MCP bridge"
        ) from exc
    return AsyncReverseBrowser
