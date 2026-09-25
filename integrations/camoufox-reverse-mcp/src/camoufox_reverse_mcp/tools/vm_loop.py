"""vm_loop.py - 通用解释器循环轨迹工具（第二阶段 Task 2）。

install：对匹配 document URL 注册 route，改写 HTML 内联脚本中的 while(true)
        派发循环，注入 tick；同时把 tick 运行时以主世界持久脚本安装。
log：   主世界 drain 轨迹并写入 session raw/。
stop：  移除 route。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.request
import uuid
from urllib.parse import urlsplit

from ..server import mcp, browser_manager
from ..network_evidence import init_script
from ..utils.loop_rewriter import rewrite_inline_scripts
from .instrumentation import _clean_response_headers

_LOOP_ROUTES: dict[str, dict] = {}

_JS_CONTENT_TYPES = ("application/javascript", "text/javascript",
                     "application/x-javascript", "application/ecmascript",
                     "text/ecmascript")


def _redirect_passthrough(is_document: bool, status, headers: dict) -> bool:
    """document 导航的 3xx 重定向必须交还浏览器遍历（第十三阶段收口）。

    route.fetch() 默认跟随重定向：对 document 请求，302 链的每一跳被
    route 层在内部消费，浏览器只收到最终 200 且地址栏停在原 URL
    （signup.live.com 实测：302 → login.srf → ?lic=1 链整体消失，
    插桩侧终点停在无 ?lic=1 的 URL，下游 risk/initialize 预检随之缺失）。
    对「document 请求 + 3xx 状态 + Location 头」的响应改为原样 fulfill，
    浏览器自行发出下一跳 document 请求并再次命中本 route，链被完整遍历。
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    location = ""
    for key, value in (headers or {}).items():
        if key.lower() == "location":
            location = value
            break
    return is_document and 300 <= code < 400 and bool(location)


def rewrite_js_body(body_bytes: bytes, content_type: str, url: str = "",
                    state_vars: list[str] | None = None,
                    max_loops: int = 50) -> tuple[bytes | None, dict]:
    """对外部 JS 响应体做 while(true) 循环插桩（第五阶段 Task 3）。

    返回 (改写后的字节, stats)；不适用于 JS 的 content-type 返回
    (None, {"skipped": True})；解析失败返回 (原字节, stats) 不破坏页面。
    """
    from ..utils.loop_rewriter import inject_loop_ticks
    ctype = content_type.lower()
    if not any(t in ctype for t in _JS_CONTENT_TYPES) and \
            not urlsplit_path_endswith_js(url):
        return None, {"skipped": True}
    try:
        text = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = body_bytes.decode("latin-1")
    rewritten, stats = inject_loop_ticks(text, state_vars=state_vars,
                                         max_loops=max_loops)
    if rewritten is None or not stats.get("loops"):
        stats.setdefault("loops", 0)
        return body_bytes, stats
    return rewritten.encode("utf-8"), stats


def urlsplit_path_endswith_js(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).path.lower().endswith(".js")
    except Exception:
        return False


def _has_js_syntax_feature(text: str) -> bool:
    """文本是否含任一 JS 语法特征（运算符/关键字形态）。

    只用于「这坨字节长得像不像 JS」的粗判，不做严格解析——
    9 字节的 "Not Found" 之类错误页一个特征都不含。
    """
    return any(tok in text for tok in (
        "=", ";", "{", "}", "(", ")", "=>",
        "function", "return", "var ", "let ", "const "))


def _classify_parse_failure(body_bytes: bytes, content_type: str) -> str:
    """第十二阶段：parse failure 归因分类。

    route 层的 parse_failures 原来混装两类失败，本函数拆开归因：
    - "nonscript"：非脚本内容被当脚本解析——典型如 404 的 9 字节
      "Not Found" 错误页（leboncoin auth chunk 实测形态），因 URL 后缀
      .js 进了改写管线，esprima 自然解析不了。判定依据（任一命中）：
        a) body 明显不是 JS：长度极小（<=64B，错误页/空响应体量级）
           且不含任何 JS 语法特征；
        b) content-type 非 JS 且 body 像错误页（空体 / "<" 开头的
           HTML 形态 / 无 JS 语法特征）。
    - "syntax"：真实 JS 但语法超出 esprima 口径（ES2017，如 ?. / ?? /
      顶层 await 等），属改写器能力边界而非内容误判。
    """
    ctype = (content_type or "").lower()
    is_js_ct = any(t in ctype for t in _JS_CONTENT_TYPES)
    text = body_bytes.decode("utf-8", errors="replace").strip()
    has_feature = _has_js_syntax_feature(text)
    # 特征 a：body 明显不是 JS（长度极小且不含 JS 语法特征）
    if len(body_bytes) <= 64 and not has_feature:
        return "nonscript"
    # 特征 b：content-type 非 JS 且 body 像错误页
    if not is_js_ct and (not text or text.startswith("<") or not has_feature):
        return "nonscript"
    return "syntax"


def _runtime_js(max_states: int, state_vars: list[str] | None = None,
                src_filter: str = "", max_sources: int = 200,
                tick_times: bool = False,
                code_patches: list[dict] | None = None) -> str:
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
    with open(os.path.join(hooks_dir, "vm_loop_trace.js"), encoding="utf-8") as f:
        template = f.read()
    return (template
            .replace("{{MAX_STATES}}", str(max_states))
            .replace("{{STATE_VARS}}", json.dumps(state_vars or []))
            .replace("{{SRC_FILTER}}", json.dumps(src_filter))
            .replace("{{MAX_SOURCES}}", str(max_sources))
            .replace("{{TICK_TIMES}}", "true" if tick_times else "false")
            .replace("{{CODE_PATCHES}}", json.dumps(code_patches or [])))


@mcp.tool()
async def vm_loop_trace(
    action: str,
    url_pattern: str = "",
    state_vars: list[str] | None = None,
    max_states_per_loop: int = 10000,
    max_loops: int = 50,
    src_filter: str = "",
    max_sources: int = 200,
    tick_times: bool = False,
    code_patches: list[dict] | None = None,
    clear: bool = False,
) -> dict:
    """Trace generic while-loop interpreter dispatch loops in page documents.

    Args:
        action: "install" (rewrite inline scripts of matched documents AND
                hook eval/Function so runtime-generated code is instrumented),
                "log" (drain per-loop iteration counts and state snapshots),
                "stop" (remove route).
        url_pattern: For "install"/"stop" — document URL glob, e.g.
            "**/v3/signin/identifier*". Route must be installed BEFORE navigate.
        state_vars: Variable names to snapshot at each loop head (e.g. the VM's
            dispatch state variables). None = count iterations only.
        max_states_per_loop: Cap on recorded snapshots per loop.
        max_loops: Max instrumented loops per inline script.
        src_filter: Only instrument dynamic sources containing this substring
            (empty = all).
        max_sources: Max dynamic-source metadata entries to record.
        tick_times: Also record per-tick timestamps relative to each loop's
            first tick (ms), aligned with native PropertyTracer wall-clock —
            for native↔VM timing correlation. Off by default (hot-loop cost).
        code_patches: For "install" — generic code patches applied to dynamic
            sources after tick injection. Each item:
            {"name": str, "pattern": regex str, "replacement": str,
             "flags": optional regex flags,
             "structural": optional bool — rename-proof matching: identifiers
             in the source are length-preserved-normalized to '_' runs before
             matching (keywords and .property names kept literal), so write
             identifiers in the pattern as _+ ; $1..$9 in the replacement are
             restored to the real names from the original source}.
            globalThis.__mcp_vm_counters; "log" merges counters across realms.
        clear: For "log" — clear the in-page buffer after draining.

    Returns:
        install: route status; log: loops with iterations/states + overhead.
    """
    if action == "install":
        return await _install(url_pattern, state_vars, max_states_per_loop,
                              max_loops, src_filter, max_sources, tick_times,
                              code_patches)
    if action == "log":
        return await _drain_log(clear)
    if action == "stop":
        return await _stop(url_pattern or None)
    return {"error": f"unknown action: {action}. Use install/log/stop"}


_LOOP_BACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# 第十三阶段复现裁决（hCaptcha widget iframe 实测）：当文档 CSP 的
# script-src/default-src 含 hash 源（'sha256-...'）时，内联脚本按哈希白名单
# 放行——任何改写都会使哈希不匹配、整段内联脚本被 CSP 阻止，widget 类页面
# 直接失去引导逻辑（hcaptcha.html 实测：checksiteconfig/hsw.js 均不再发出）。
# 此时必须放弃内联改写、原样透传，用 stats["skipped_csp_hash"] 如实登记。
_CSP_META_RE = re.compile(
    r"<meta[^>]+http-equiv=[\"']?content-security-policy[\"']?[^>]*>",
    re.IGNORECASE)
_CSP_CONTENT_ATTR_RE = re.compile(
    r"content=(\"[^\"]*\"|'[^']*')", re.IGNORECASE)
_CSP_HASH_SOURCE_RE = re.compile(r"'sha(?:256|384|512)-")


def csp_blocks_inline_rewrite(headers: dict, html: str) -> bool:
    """文档 CSP 是否禁止改写内联脚本（hash 源在位即禁止）。

    判定口径：script-src（缺省回落 default-src）含 'sha256/384/512-' hash 源。
    按 CSP3，hash/nonce 源在位时 'unsafe-inline' 被忽略，故只要 hash 在位，
    改写后的内联脚本必被阻止，无需再判 'unsafe-inline'。
    """
    csp_values = [v for k, v in (headers or {}).items()
                  if k.lower() == "content-security-policy"]
    for tag in _CSP_META_RE.findall(html[:16384] or ""):
        m = _CSP_CONTENT_ATTR_RE.search(tag)
        if m:
            csp_values.append(m.group(1)[1:-1])
    for csp in csp_values:
        directives = csp.lower()
        m = re.search(r"script-src\s+([^;]+)", directives)
        src = m.group(1) if m else None
        if src is None:
            m = re.search(r"default-src\s+([^;]+)", directives)
            src = m.group(1) if m else None
        if src and _CSP_HASH_SOURCE_RE.search(src):
            return True
    return False


def _is_loopback_url(url: str) -> bool:
    try:
        return (urlsplit(url).hostname or "").lower() in _LOOP_BACK_HOSTS
    except Exception:
        return False


async def _loopback_fetch(route):
    """route.fetch() 对 loopback（127.0.0.1/localhost/::1）必 socket hang up
    （第十阶段切片 B 实测：python/node 服务器均复现，真实站点正常）。
    对 loopback URL 改用 urllib 在线程里重取，返回 (status, headers, body)，
    headers 键统一小写以对齐 Playwright 形态；非 loopback 或失败返回 None。"""
    req = route.request
    if not _is_loopback_url(req.url):
        return None

    def _do():
        data = None
        if req.method not in ("GET", "HEAD"):
            pd = req.post_data
            if isinstance(pd, str):
                data = pd.encode("utf-8")
            elif isinstance(pd, (bytes, bytearray)):
                data = bytes(pd)
        headers = {k: v for k, v in req.headers.items()
                   if k.lower() not in ("host", "content-length", "connection")}
        r = urllib.request.Request(req.url, data=data, headers=headers,
                                   method=req.method)
        with urllib.request.urlopen(r, timeout=15) as resp:
            return (resp.status,
                    {k.lower(): v for k, v in resp.headers.items()},
                    resp.read())

    try:
        return await asyncio.to_thread(_do)
    except Exception:
        return None


async def _install(url_pattern, state_vars, max_states, max_loops,
                   src_filter="", max_sources=200, tick_times=False,
                   code_patches=None) -> dict:
    if not url_pattern:
        return {"error": "url_pattern is required for action='install'"}
    try:
        ctx = browser_manager.contexts.get("default")
        if ctx is None:
            await browser_manager._ensure_browser()
            ctx = browser_manager.contexts.get("default")
        if ctx is None:
            return {"error": "no browser context available"}

        # tick 运行时 + eval/Function 动态注入钩子必须先于页面脚本存在于主世界。
        runtime_src = _runtime_js(max_states, state_vars, src_filter,
                                  max_sources, tick_times, code_patches)
        runtime = "mw:" + runtime_src
        await browser_manager.add_persistent_script(
            f"vm_loop_trace:{url_pattern}", runtime)

        stats = {"documents_rewritten": 0, "loops": 0, "parse_failures": 0,
                 "parse_failures_syntax": 0, "parse_failures_nonscript": 0,
                 "js_rewritten": 0, "route_hits": 0, "route_errors": 0,
                 "worker_urls": []}

        async def route_handler(route):
            stats["route_hits"] += 1
            # 第十三阶段：document 导航请求禁止 route.fetch 内部跟随重定向
            # （否则 302 链被 route 层吞掉，浏览器地址栏停在原 URL）。
            is_document = False
            try:
                is_document = route.request.resource_type == "document"
            except Exception:
                pass
            try:
                resp = await route.fetch(
                    **({"max_redirects": 0} if is_document else {}))
                status = resp.status
                resp_headers = resp.headers
                body_bytes = await resp.body()
            except Exception as e:
                # loopback 绕行：route.fetch 对 127.0.0.1/localhost 必挂，
                # 改用 urllib 重取后走同一改写管线（切片 B）。
                fb = await _loopback_fetch(route)
                if fb is None:
                    stats["route_errors"] += 1
                    stats["last_error"] = str(e)[:200]
                    try:
                        await route.continue_()
                    except Exception:
                        pass
                    return
                stats["route_fetch_fallback"] = \
                    stats.get("route_fetch_fallback", 0) + 1
                status, resp_headers, body_bytes = fb
            if _redirect_passthrough(is_document, status, resp_headers):
                # document 级 3xx 原样交还浏览器：浏览器自行遍历下一跳，
                # 新 document 请求会再次命中本 route（链上每跳各自改写）。
                stats["redirects_passthrough"] = \
                    stats.get("redirects_passthrough", 0) + 1
                await route.fulfill(
                    status=status,
                    headers=_clean_response_headers(resp_headers),
                    body=body_bytes)
                return
            try:
                content_type = resp_headers.get("content-type", "")
                stats.setdefault("content_types", {})
                ct_key = content_type.split(";")[0] or "(none)"
                stats["content_types"][ct_key] = \
                    stats["content_types"].get(ct_key, 0) + 1
                if "text/html" in content_type:
                    try:
                        html = body_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        html = body_bytes.decode("latin-1")
                    if csp_blocks_inline_rewrite(resp_headers, html):
                        # CSP hash 白名单文档：改写内联脚本必被阻止，原样透传
                        stats["skipped_csp_hash"] = \
                            stats.get("skipped_csp_hash", 0) + 1
                        await route.fulfill(
                            status=status,
                            headers=_clean_response_headers(resp_headers),
                            body=body_bytes)
                        return
                    rewritten, rstats = rewrite_inline_scripts(
                        html, state_vars=state_vars, max_loops=max_loops)
                    for key in ("scripts_rewritten", "loops", "parse_failures"):
                        stats[key if key != "scripts_rewritten" else "documents_rewritten"] += rstats[key]
                    # 第十二阶段：HTML 内联 <script> 的内容按定义是 JS，
                    # 其解析失败一律归 syntax（esprima 口径不够新），
                    # 不存在「非脚本被当脚本」的形态。
                    stats["parse_failures_syntax"] += rstats["parse_failures"]
                    headers = _clean_response_headers(resp_headers)
                    headers["content-type"] = content_type.split(";")[0] + "; charset=utf-8"
                    await route.fulfill(status=status, headers=headers, body=rewritten)
                    return
                # 第五阶段 Task 3：外部 script src 的 JS 响应也插桩
                # （抖音 JSVMP 实战缺口：安全 SDK 全走外链脚本）。
                # 第十一阶段：Worker 脚本（resource_type=worker 或
                # sec-fetch-dest=worker）在 tick 改写后前置完整运行时——
                # Worker realm 没有 init script 安装的运行时（页面内
                # 同步 XHR 取源码可能被 CSP connect-src 拦，Shopify
                # web-pixels 实测），route 层是它唯一的插桩入口。
                is_worker = False
                try:
                    is_worker = route.request.resource_type == "worker"
                except Exception:
                    pass
                if not is_worker:
                    dest = (route.request.headers or {}).get(
                        "sec-fetch-dest", "")
                    is_worker = dest in ("worker", "sharedworker")
                new_body, jstats = rewrite_js_body(
                    body_bytes, content_type, url=route.request.url,
                    state_vars=state_vars, max_loops=max_loops)
                if new_body is None and is_worker:
                    # 无循环的 worker 脚本也要前置运行时（drain 桥/值事件
                    # 通道在运行时里）；rewrite_js_body 对非 JS 返回 None
                    # 时才放行，worker 且是 JS 内容则强制处理
                    ct_lower = content_type.lower()
                    if any(t in ct_lower for t in _JS_CONTENT_TYPES) or \
                            "text/plain" in ct_lower or not ct_lower:
                        new_body = body_bytes
                        jstats = {"loops": 0}
                if new_body is None:
                    # 第十一阶段（gsxt 实测缺口）：不改写的响应也必须用
                    # 已取回的响应 fulfill，不能 continue_——fetch() 之后
                    # continue_ 会让同一请求物理发两次，令牌类反爬挑战页
                    # （521 + 动态令牌）第二次命中直接返回错误页，流程卡死。
                    await route.fulfill(
                        status=status,
                        headers=_clean_response_headers(resp_headers),
                        body=body_bytes)
                    return
                if is_worker:
                    try:
                        new_body = (runtime_src + "\n").encode() + new_body
                        stats["worker_urls"].append(route.request.url[:200])
                        stats["workers_rewritten"] = \
                            stats.get("workers_rewritten", 0) + 1
                    except Exception:
                        pass
                if jstats.get("loops"):
                    stats["js_rewritten"] += 1
                    stats["loops"] += jstats["loops"]
                if jstats.get("error"):
                    stats["parse_failures"] += 1
                    # 第十二阶段：拆分归因——真实 JS 语法超 esprima 口径
                    # （syntax）vs 非脚本内容被当脚本解析（nonscript，
                    # 如 404 的 9 字节 "Not Found" 错误页走 .js URL）。
                    stats["parse_failures_" + _classify_parse_failure(
                        body_bytes, content_type)] += 1
                headers = _clean_response_headers(resp_headers)
                if jstats.get("loops"):
                    headers["content-type"] = content_type.split(";")[0] + "; charset=utf-8"
                await route.fulfill(status=status, headers=headers,
                                    body=new_body)
            except Exception as e:
                stats["route_errors"] += 1
                stats["last_error"] = str(e)[:200]
                try:
                    await route.continue_()
                except Exception:
                    pass

        await ctx.route(url_pattern, route_handler)
        _LOOP_ROUTES[url_pattern] = {"context": ctx, "stats": stats,
                                     "state_vars": state_vars}
        return {
            "status": "tracing", "pattern": url_pattern,
            "state_vars": state_vars, "route_level": "context",
            "note": "Route active; tick runtime installed in main world. "
                    "Navigate or reload to trigger rewriting.",
        }
    except Exception as e:
        return {"error": str(e)}


async def _drain_log(clear: bool) -> dict:
    try:
        page = await browser_manager.get_active_page()
        # drainAllAsyncText：聚合顶层 + 各同源 iframe + 各 Worker realm 的轨迹
        # （Worker 经消息桥异步回传，evaluate 会 await Promise；Worker 内嵌套
        # Worker 由 Worker 侧桥递归聚合）。回退链：drainAllAsyncText →
        # drainAllText（旧版运行时无 Worker 聚合）→ drainText（更旧）。
        raw = await page.evaluate(
            "mw:() => (window.__mcp_vm_loop_state ? "
            "(window.__mcp_vm_loop_state.drainAllAsyncText ? "
            "window.__mcp_vm_loop_state.drainAllAsyncText(1200) : "
            "(window.__mcp_vm_loop_state.drainAllText ? "
            "window.__mcp_vm_loop_state.drainAllText() : "
            "window.__mcp_vm_loop_state.drainText())) : null)")
        if raw is None:
            return {"error": "vm_loop runtime not present in page main world; "
                             "install before navigate or reload"}
        data = json.loads(raw)
        realms = data if isinstance(data, list) else [{"realm": "top", "data": data}]

        # Worker realm 的 data 是 drainAllAsync 的 realm 数组（含嵌套 Worker），
        # 递归展平为 "<父realm>/<子realm>" 路径，保持 loops 列表扁平。
        def _flatten(entries, prefix=""):
            flat = []
            for entry in entries:
                realm = prefix + entry.get("realm", "?")
                edata = entry.get("data")
                if isinstance(edata, list):  # Worker 桥回传：嵌套 realm 数组
                    flat.extend(_flatten(edata, realm + "/"))
                else:
                    flat_entry = {"realm": realm, "data": edata}
                    if "error" in entry:
                        flat_entry["error"] = entry["error"]
                    flat.append(flat_entry)
            return flat

        realms = _flatten(realms)
        loops = []
        sources = []
        wasm_modules = []
        value_taps = []
        trace_seq = []
        trace_seq_overflow = 0
        string_primitives: dict = {}
        counters: dict = {}
        overhead = {"tick_calls": 0, "tick_overhead_ns": 0, "per_tick_ns": 0}

        # 代码补丁产物（Task 5）：各 realm 的 __mcp_vm_counters 深合并，
        # 数值叶子求和、字典递归，类型冲突时后者覆盖。
        def _merge_counters(dst: dict, src: dict) -> None:
            for key, val in src.items():
                if isinstance(val, dict) and isinstance(dst.get(key), dict):
                    _merge_counters(dst[key], val)
                elif (isinstance(val, (int, float))
                      and isinstance(dst.get(key), (int, float))):
                    dst[key] += val
                else:
                    dst[key] = val

        for entry in realms:
            rdata = entry.get("data") or {}
            if not isinstance(rdata, dict):
                continue
            for loop in rdata.get("loops", []):
                loop["realm"] = entry.get("realm")
                loops.append(loop)
            for src in rdata.get("dynamic_sources", []):
                src["realm"] = entry.get("realm")
                sources.append(src)
            if isinstance(rdata.get("counters"), dict):
                _merge_counters(counters, rdata["counters"])
            # wasm 模块证据（第六阶段）：跨 realm 汇总，每项打 realm 标签
            for wm in rdata.get("wasm_modules") or []:
                wm["realm"] = entry.get("realm")
                wasm_modules.append(wm)
            # 值变换事件（第七阶段）：跨 realm 汇总，打 realm 标签，
            # 按 ts 排序保证离线关联器的时序可读性
            for vt in rdata.get("value_taps") or []:
                vt["realm"] = entry.get("realm")
                value_taps.append(vt)
            # 字符串装配原语计数（第八阶段）：跨 realm 求和
            for sk, sv in (rdata.get("string_primitives") or {}).items():
                if isinstance(sv, (int, float)):
                    string_primitives[sk] = string_primitives.get(sk, 0) + sv
            # opcode 序列记录（第九阶段）：跨 realm 汇总，打 realm 标签
            for ev in rdata.get("trace_seq") or []:
                ev["realm"] = entry.get("realm")
                trace_seq.append(ev)
            trace_seq_overflow += rdata.get("trace_seq_overflow") or 0
            oh = rdata.get("overhead") or {}
            overhead["tick_calls"] += oh.get("tick_calls", 0)
            overhead["tick_overhead_ns"] += oh.get("tick_overhead_ns", 0)
        # worker-gap 源码取回（第十一阶段）：页面内同步 XHR 被 CSP
        # connect-src 拦截、且 Worker 在 sandbox null-principal realm 根本
        # 无法创建（请求不过 route，route 层插桩够不着）时，用
        # context.request（共享上下文 Cookie、不受页面 CSP 约束）把
        # worker 脚本取回落盘，产物对离线分析仍是完整的。取回失败如实记
        # fetched.error。
        gap_urls = []
        for src in sources:
            if src.get("kind") in ("worker-gap", "worker-blocked"):
                u = (src.get("source") or "").split(" :: ")[0]
                if isinstance(u, str) and u.startswith(("http://", "https://")) \
                        and u not in gap_urls:
                    gap_urls.append(u)
        worker_recovery = None
        if gap_urls:
            worker_recovery = {"fetched": 0, "failed": 0}
            try:
                req_ctx = page.context.request
            except Exception:
                req_ctx = None
            for u in gap_urls[:20]:
                rec = None
                if req_ctx is not None:
                    try:
                        resp = await req_ctx.get(u, timeout=15000)
                        body = await resp.body()
                        rec = {"status": resp.status, "length": len(body)}
                        if 200 <= resp.status < 300 and body:
                            worker_recovery["fetched"] += 1
                            if browser_manager.session is not None:
                                path = browser_manager.write_artifact(
                                    None, body, "worker-source",
                                    default=f"raw/worker-source/"
                                            f"{uuid.uuid4().hex}.js")
                                rec["file"] = str(path.resolve())
                            elif len(body) <= 262144:
                                rec["source"] = body.decode(
                                    "utf-8", errors="replace")
                        else:
                            rec["error"] = f"http {resp.status}"
                            worker_recovery["failed"] += 1
                    except Exception as e:
                        rec = {"error": str(e)[:200]}
                        worker_recovery["failed"] += 1
                else:
                    worker_recovery["failed"] += 1
                for src in sources:
                    if src.get("kind") in ("worker-gap", "worker-blocked") and \
                            (src.get("source") or "").split(" :: ")[0] == u:
                        src["fetched"] = rec
        if overhead["tick_calls"]:
            overhead["per_tick_ns"] = round(
                overhead["tick_overhead_ns"] / overhead["tick_calls"])
        value_taps.sort(key=lambda v: v.get("ts", 0))
        # 跨 realm 时序排序；同 ts 按 realm 内 seq 保序
        trace_seq.sort(key=lambda e: (e.get("ts", 0), e.get("seq", 0)))
        data = {"realms": realms, "loops": loops, "dynamic_sources": sources,
                "counters": counters or None, "overhead": overhead,
                "wasm_modules": wasm_modules or None,
                "value_taps": value_taps or None,
                "trace_seq": trace_seq or None,
                "trace_seq_overflow": trace_seq_overflow or None,
                "worker_recovery": worker_recovery,
                "string_primitives": string_primitives or None}
        # 覆盖率自描述：每个循环与总体都给 recorded/iterations 百分比，
        # 截断不再需要从 truncated 标志间接推断；无快照变量的循环
        # （纯计数，states 必然为空）标 None 表示 n/a，不计入分母
        for loop in loops:
            it = loop.get("iterations", 0)
            rec = loop.get("states_recorded", 0)
            if it and rec == 0 and not loop.get("truncated"):
                loop["coverage_pct"] = None  # 无快照变量，纯计数
            else:
                loop["coverage_pct"] = round(rec / it * 100, 1) if it else 100.0
        measurable = [l for l in loops if l.get("coverage_pct") is not None]
        total_it = sum(l.get("iterations", 0) for l in measurable)
        data["coverage_pct"] = round(
            sum(l.get("states_recorded", 0) for l in measurable) / total_it * 100, 1
        ) if total_it else 100.0
        if clear:
            await page.evaluate("mw:() => window.__mcp_vm_loop_state.clear()")
        artifact = None
        if browser_manager.session is not None:
            # 第十一阶段（gsxt 实测缺口）：混淆 JS 的源码/状态字符串可能含
            # 孤立代理项（lone surrogate），ensure_ascii=False + utf-8
            # encode 会直接抛错、整个 drain 产物丢失。ensure_ascii=True
            # 把代理项转义为 \udXXX，JSON 语义不变、可无损 round-trip。
            payload = json.dumps({"ts": int(time.time() * 1000), **data},
                                 ensure_ascii=True).encode()
            # 第十阶段切片 E1：流式落盘——payload 超阈值（32MB）时把
            # loops[].states 拆到旁车 NDJSON（每行 {loop, realm, i, state}），
            # 主 JSON 只留元数据 + states_file 指针，避免超大单文件撑爆
            # 读取端；未超阈值保持原单文件形态（小 payload 不拆）。
            if len(payload) > 32 * 1024 * 1024:
                art_id = uuid.uuid4().hex
                states_rel = f"raw/vm-loop/{art_id}-states.ndjson"
                lines = []
                n_states = 0
                for loop in loops:
                    st = loop.get("states") or []
                    if not st:
                        continue
                    for i, sv in enumerate(st):
                        lines.append(json.dumps(
                            {"loop": loop.get("loop"),
                             "realm": loop.get("realm"),
                             "i": i, "state": sv},
                            ensure_ascii=True))
                        n_states += 1
                    loop.pop("states", None)
                    loop["states_file"] = states_rel
                if n_states:
                    browser_manager.write_artifact(
                        None, ("\n".join(lines) + "\n").encode(),
                        "vm-loop-states", default=states_rel)
                    data["states_streamed"] = {
                        "file": states_rel, "states": n_states,
                        "format": "ndjson: {loop, realm, i, state}"}
                    payload = json.dumps({"ts": int(time.time() * 1000),
                                          **data},
                                         ensure_ascii=True).encode()
            artifact = str(browser_manager.write_artifact(
                None, payload, "vm-loop-trace",
                default=f"raw/vm-loop/{uuid.uuid4().hex}.json").resolve())
        return {
            "status": "ok",
            "loops_observed": len(loops),
            "total_iterations": sum(l.get("iterations", 0) for l in loops),
            "counters": counters or None,
            "overhead": overhead,
            "loops": loops,
            "wasm_modules": wasm_modules or None,
            "value_taps": value_taps or None,
            "trace_seq": trace_seq or None,
            "worker_recovery": worker_recovery,
            "artifact": artifact,
        }
    except Exception as e:
        return {"error": str(e)}


async def _stop(url_pattern) -> dict:
    removed = []
    patterns = [url_pattern] if url_pattern else list(_LOOP_ROUTES.keys())
    for pattern in patterns:
        info = _LOOP_ROUTES.pop(pattern, None)
        if not info:
            continue
        try:
            await info["context"].unroute(pattern)
        except Exception:
            pass
        removed.append({"pattern": pattern,
                        "stats": info.get("stats")})
    return {"status": "stopped", "removed": removed}
