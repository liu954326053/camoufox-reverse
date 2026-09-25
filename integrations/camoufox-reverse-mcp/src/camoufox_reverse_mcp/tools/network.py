from __future__ import annotations

import json
import time
import uuid

from ..server import mcp, browser_manager


def _error(code: str, message: str) -> dict:
    return {"status": "error", "error": {"code": code, "message": message}}


def _active_session_or_error() -> dict | None:
    if browser_manager.runtime is None or browser_manager.session is None:
        return _error(
            "no_active_session",
            "Call launch_browser(project_dir=...) before using network capture.",
        )
    return None


def _capture_event(action: str, *, url_pattern: str, capture_body: bool) -> str:
    payload = {
        "action": action,
        "url_pattern": url_pattern,
        "capture_body": capture_body,
        "capture_profile": "raw",
        "timestamp": int(time.time() * 1000),
    }
    artifact = browser_manager.write_artifact(
        None,
        (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
        "network-capture-control",
        default=f"raw/network/capture/{uuid.uuid4().hex}.jsonl",
    )
    return str(artifact.resolve())


def _capture_response(action: str, artifact: str | None = None, **extra) -> dict:
    result = {
        "status": action,
        "capture_profile": "raw",
        **browser_manager.session_metadata(),
    }
    if artifact:
        result.setdefault("artifacts", {})["capture"] = artifact
    result.update(extra)
    return result


@mcp.tool()
async def network_capture(
    action: str,
    url_pattern: str = "**/*",
    capture_body: bool = False,
) -> dict:
    """Unified network capture control (v0.9.0).

    Replaces start_network_capture / stop_network_capture.

    Args:
        action:
          "start"  — begin capturing network events
          "stop"   — stop capturing (buffer retained)
          "clear"  — clear the capture buffer
          "status" — return current capture state
        url_pattern: Glob pattern for "start" (default "**/*" captures all).
        capture_body: For "start" only; capture response bodies (more memory).

    Returns:
        dict with action result + current status snapshot.
    """
    error = _active_session_or_error()
    if error:
        return error
    if action == "start":
        browser_manager._capturing = True
        browser_manager._capture_pattern = url_pattern
        browser_manager._capture_body = capture_body
        artifact = _capture_event(
            "start", url_pattern=url_pattern, capture_body=capture_body
        )
        return _capture_response(
            "started", artifact, pattern=url_pattern, capture_body=capture_body
        )
    elif action == "stop":
        browser_manager._capturing = False
        # 第十二阶段：停止捕获时把仍在途请求登记为 pending-at-close
        pending_at_close = browser_manager.finalize_pending_requests()
        artifact = _capture_event(
            "stop",
            url_pattern=browser_manager._capture_pattern,
            capture_body=browser_manager._capture_body,
        )
        return _capture_response(
            "stopped", artifact, total_requests=len(browser_manager._network_requests),
            pending_at_close=pending_at_close,
        )
    elif action == "clear":
        await browser_manager.persist_before_navigation()
        count = len(browser_manager._network_requests)
        browser_manager._network_requests.clear()
        artifact = _capture_event(
            "clear",
            url_pattern=browser_manager._capture_pattern,
            capture_body=browser_manager._capture_body,
        )
        return _capture_response("cleared", artifact, cleared_count=count)
    elif action == "status":
        artifact = _capture_event(
            "status",
            url_pattern=browser_manager._capture_pattern,
            capture_body=browser_manager._capture_body,
        )
        return _capture_response(
            "ok",
            artifact,
            active=browser_manager._capturing,
            pattern=browser_manager._capture_pattern,
            capture_body=browser_manager._capture_body,
            buffer_size=len(browser_manager._network_requests),
            engine_initiator_stats=dict(browser_manager._engine_initiator_stats),
            engine_body_stats=dict(browser_manager._engine_body_stats),
        )
    else:
        return _error(
            "invalid_action",
            f"unknown action: {action}. Use start/stop/clear/status",
        )


@mcp.tool()
async def list_network_requests(
    url_filter: str | None = None,
    url_contains_domain: str | None = None,
    method: str | None = None,
    resource_type: str | None = None,
    status_code: int | None = None,
) -> list[dict]:
    """List captured network requests with optional filters.

    Args:
        url_filter: Substring filter for request URLs.
        url_contains_domain: Convenience domain filter (e.g. 'nmpa.gov.cn').
        method: HTTP method filter (e.g. "GET", "POST").
        resource_type: Resource type filter (e.g. "xhr", "fetch", "script", "document").
        status_code: HTTP status code filter.

    Returns:
        List of request summaries with id, url, method, status, type, ms, size.
    """
    try:
        reqs = list(browser_manager._network_requests)
        if url_filter:
            reqs = [r for r in reqs if url_filter in r["url"]]
        if url_contains_domain:
            reqs = [r for r in reqs if url_contains_domain in r.get("url", "")]
        if method:
            reqs = [r for r in reqs if r["method"].upper() == method.upper()]
        if resource_type:
            reqs = [r for r in reqs if r.get("resource_type") == resource_type]
        if status_code is not None:
            reqs = [r for r in reqs if r.get("status") == status_code]

        summaries = []
        for r in reqs:
            body_size = len(r["response_body"]) if r.get("response_body") else 0
            engine_stack = r.get("engine_initiator_stack")
            top_frame = engine_stack[0] if engine_stack else None
            summaries.append({
                "id": r["id"], "url": r["url"][:200], "method": r["method"],
                "status": r.get("status"), "type": r.get("resource_type"),
                "ms": r.get("duration"), "size": body_size,
                "has_body": body_size > 0,
                # 第十二阶段：终态自描述字段随列表输出
                "terminal_state": r.get("terminal_state"),
                "failure_reason": r.get("failure_reason"),
                "pending_at_close": bool(r.get("pending_at_close", False)),
                # 引擎层 initiator 栈（零页面污染通道）：只报顶帧摘要，
                # 全量帧见 get_network_request / get_request_initiator。
                "has_engine_initiator_stack": bool(engine_stack),
                "engine_initiator_top": (
                    f"{top_frame.get('functionName') or '(anonymous)'}"
                    f" @ {top_frame.get('filename')}:{top_frame.get('lineNumber')}"
                    if top_frame else None
                ),
                # 第十三阶段：引擎层 body（零污染通道）摘要；全量 base64 见
                # get_network_request(include_body=True)。
                "has_engine_request_body": bool(r.get("engine_request_body")),
                "has_engine_response_body": bool(r.get("engine_response_body")),
            })
        return summaries
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_network_request(
    request_id: int,
    include_body: bool = False,
    include_headers: bool = True,
    max_body_size: int = 5000,
) -> dict:
    """Get full details of a specific captured network request.

    Args:
        request_id: The ID of the request (from list_network_requests).
        include_body: Include response body (default False).
        include_headers: Include request/response headers (default True).
        max_body_size: Max chars of body when include_body=True. Pass -1 for unlimited.

    Returns:
        dict with request and response details.
    """
    try:
        for r in browser_manager._network_requests:
            if r["id"] == request_id:
                result = dict(r)
                if not include_body:
                    body = result.pop("response_body", None)
                    result["response_body_available"] = body is not None
                    if body:
                        result["response_body_size"] = len(body)
                    # 引擎层 body（base64）默认不随详情输出，只报尺寸与截断标记。
                    eng_req = result.pop("engine_request_body", None)
                    eng_resp = result.pop("engine_response_body", None)
                    result["engine_request_body_available"] = eng_req is not None
                    result["engine_response_body_available"] = eng_resp is not None
                else:
                    body = result.get("response_body")
                    if body is not None and max_body_size >= 0 and len(body) > max_body_size:
                        result["response_body"] = body[:max_body_size]
                        result["response_body_truncated"] = True
                        result["response_body_original_size"] = len(body)
                        result["response_body_size_returned"] = max_body_size
                    elif body is not None:
                        result["response_body_truncated"] = False
                        result["response_body_original_size"] = len(body)
                        result["response_body_size_returned"] = len(body)
                if not include_headers:
                    result.pop("request_headers", None)
                    result.pop("response_headers", None)
                return result
        return {"error": f"Request ID {request_id} not found"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_request_initiator(request_id: int) -> dict:
    """Read session-owned raw initiator evidence for a stable request ID.

    Two independent initiator channels are reported side by side, never merged:
    - ``initiator_stack`` / ``initiator_type``: page-world xhr/fetch hook
      correlation (requires hooks installed before navigation; polluted page
      world; richer async context when available).
    - ``engine_initiator_stack``: engine-level stack captured chrome-side in
      the content process at channel open (zero page-world pollution; the only
      channel usable on zero-tolerance targets). ``None`` when the channel was
      not opened synchronously from page JS (browser-initiated, navigation,
      worker, internal retry) or when the driver patch is not applied.

    Equal requests within a frame and document are reported as ambiguous for
    the hook channel, never matched by URL or FIFO.
    Evidence remains queryable after navigation and capture-buffer clearing.
    """
    try:
        evidence = browser_manager.network_evidence
        await evidence.checkpoint()
        target_entry = evidence.by_id.get(request_id)
        if target_entry is None:
            target_entry = next((r for r in browser_manager._network_requests
                                 if r["id"] == request_id), None)
        if target_entry is None:
            return {"error": f"Request ID {request_id} not found"}
        result = evidence.initiator(target_entry)
        result["engine_initiator_stack"] = target_entry.get("engine_initiator_stack")
        result["diagnostics"]["engine_initiator_stats"] = dict(
            browser_manager._engine_initiator_stats)
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def intercept_request(
    url_pattern: str,
    action: str = "log",
    modify_headers: dict | None = None,
    modify_body: str | None = None,
    mock_response: dict | None = None,
) -> dict:
    """Intercept network requests matching a pattern.

    Args:
        url_pattern: URL glob pattern (e.g. "**/api/login*").
        action: "log", "block", "modify", "mock", or "stop" (unroute).
        modify_headers: Headers to add/override (action="modify").
        modify_body: Request body replacement (action="modify").
        mock_response: Dict with "status", "headers", "body" (action="mock").
    """
    try:
        page = await browser_manager.get_active_page()

        if action == "stop":
            if url_pattern:
                await page.unroute(url_pattern)
                return {"status": "stopped", "pattern": url_pattern}
            else:
                await page.unroute("**/*")
                return {"status": "stopped_all"}

        async def handler(route):
            if action == "log":
                browser_manager._console_logs.append({
                    "level": "info",
                    "text": f"[INTERCEPT:log] {route.request.method} {route.request.url}",
                    "timestamp": time.time() * 1000, "location": None,
                })
                await route.continue_()
            elif action == "block":
                await route.abort()
            elif action == "modify":
                overrides = {}
                if modify_headers:
                    overrides["headers"] = {**dict(route.request.headers), **modify_headers}
                if modify_body:
                    overrides["post_data"] = modify_body
                await route.continue_(**overrides)
            elif action == "mock":
                resp = mock_response or {}
                await route.fulfill(
                    status=resp.get("status", 200),
                    headers=resp.get("headers", {"content-type": "application/json"}),
                    body=resp.get("body", "{}"),
                )

        await page.route(url_pattern, handler)
        return {"status": "intercepting", "pattern": url_pattern, "action": action}
    except Exception as e:
        return {"error": str(e)}
