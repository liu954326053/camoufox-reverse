#!/usr/bin/env python3
"""Generic VM-target probe (Phase 5 Task 1).

Passively loads a protected site with the reverse browser, installs the
vm_loop_trace instrumentation on all documents, lets any VM initialize and
run, drains all realm traces + network evidence + native trace, then scores
the 10-point coverage checklist from docs/vm-reverse-capability-model-2026-09-25.md
into derived/coverage-scorecard.json.

Observation-only: never submits credentials, never triggers risk-control
actions; just loads the page and records what the target's JS does.
"""

import argparse
import json
import os
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "pythonlib"))
sys.path.insert(0, str(ROOT))  # adapters
from camoufox_reverse_mcp_client import MCPStdioClient
from camoufox.reverse_compat import BROWSER_SELECTOR

# 加密参数常见签名（URL query / header 名），用于评分卡第 5 条的候选定位
ENCRYPTED_PARAM_HINTS = (
    "x-bogus", "xbogus", "msToken".lower(), "_signature", "signature",
    "ttwid", "__ac_signature", "__ac_nonce", "x-tt-", "x-ss-",
    "sec-ch-", "x-kpsdk", "reese84", "sensor_data", "abck",
)


def unpack(reply):
    values = [json.loads(item["text"]) for item in reply.get("content", [])
              if item.get("type") == "text"]
    value = values[0] if len(values) == 1 else values
    if reply.get("isError") or isinstance(value, dict) and value.get("error"):
        raise RuntimeError(
            f"MCP tool failed: {json.dumps(value, ensure_ascii=False)[:400]}")
    return value


def run(args):
    project = args.project_dir.resolve()
    project.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_dir = project / "validation" / uuid.uuid4().hex
    report_dir.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    transcript = []
    summary = {"status": "failed", "report_dir": str(report_dir),
               "target_url": args.target_url}
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "pythonlib"), str(ROOT / "integrations/camoufox-reverse-mcp/src"),
         environment.get("PYTHONPATH", "")])
    server_args = ["-m", "camoufox_reverse_mcp", "--project-dir", str(project),
                   "--headless", "--os", "macos", "--locale", "en-US"]
    if args.proxy:
        server_args += ["--proxy", args.proxy]

    with MCPStdioClient(args.python, args=server_args,
                        env=environment, timeout=120) as client:
        client.initialize()

        def call(name, **arguments):
            (report_dir / "progress.json").write_text(
                json.dumps({"tool": name, "state": "running"}))
            reply = client.call_tool(name, arguments)
            transcript.append({"tool": name, "args": arguments, "result": reply})
            (report_dir / "mcp-transcript.json").write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2))
            (report_dir / "progress.json").write_text(
                json.dumps({"tool": name, "state": "returned"}))
            return unpack(reply)

        launched = False
        try:
            launch_kwargs = dict(project_dir=str(project),
                                 browser_version=args.browser_version,
                                 headless=True, os_type="macos", locale="en-US",
                                 enable_trace=True, trace_profile="deep",
                                 capture_profile="raw")
            if args.proxy:
                launch_kwargs["proxy"] = args.proxy
            launch = call("launch_browser", **launch_kwargs)
            launched = True
            summary["session_id"] = launch["session_id"]
            summary["session_dir"] = launch["session_dir"]
            call("network_capture", action="start", capture_body=True)

            # 全文档安装 VM 循环插桩：目标未知时 URL 形态不可预知，
            # route 改写所有 HTML 文档，动态通道（eval/Function/TT/Worker）
            # 由持久运行时全覆盖。--code-patches 透传目标适配器锚点。
            install_kwargs = dict(url_pattern="**", tick_times=True,
                                  max_states_per_loop=200000)
            if args.code_patches:
                raw = args.code_patches
                if raw.startswith("@"):
                    raw = Path(raw[1:]).read_text()
                install_kwargs["code_patches"] = json.loads(raw)
                summary["code_patches"] = [p.get("name")
                                           for p in install_kwargs["code_patches"]]
            call("vm_loop_trace", action="install", **install_kwargs)

            try:
                call("navigate", url=args.target_url,
                     wait_until=args.wait_until,
                     pre_inject_hooks=["xhr", "fetch"],
                     clear_network_capture=False)
            except Exception as error:
                # 重站点 load 事件可能永不触发；导航超时不阻断后续探测
                summary["navigate_warning"] = str(error)[:200]

            # 让目标 VM 初始化并运行程序；等待期间目标可能导航（重定向），
            # 执行上下文会被销毁——分段等待并容忍导航中断，等满总时长。
            waited = 0.0
            while waited < args.wait_seconds:
                step = min(2.0, args.wait_seconds - waited)
                try:
                    call("evaluate_js",
                         expression=f"new Promise(r => setTimeout(r, {int(step * 1000)}))",
                         await_promise=True)
                except Exception as error:
                    summary.setdefault("wait_interruptions", []).append(
                        str(error)[:120])
                waited += step

            # 可选交互：被动加载不足以触发 VM 的目标（如 reCAPTCHA 复选框），
            # 用一个主世界表达式触发（点击演示按钮等无害动作），再等 VM 跑
            if args.click_expression:
                try:
                    summary["click_result"] = call(
                        "evaluate_js", expression=args.click_expression)
                except Exception as error:
                    summary["click_error"] = str(error)[:200]
                waited = 0.0
                while waited < args.post_click_wait_seconds:
                    step = min(2.0, args.post_click_wait_seconds - waited)
                    try:
                        call("evaluate_js",
                             expression=f"new Promise(r => setTimeout(r, {int(step * 1000)}))",
                             await_promise=True)
                    except Exception:
                        pass
                    waited += step

            def probe(name, fn):
                try:
                    summary[name] = fn()
                except Exception as error:
                    summary[name] = {"probe_error": f"{type(error).__name__}: {error}"[:300]}
                return summary[name]

            probe("page_info", lambda: {
                k: call("get_page_info").get(k) for k in ("url", "title")})

            probe("hook_state", lambda: call("evaluate_js", expression=(
                "mw:() => ({xhr_raw: !!window.__mcp_xhr_hooked, fetch_raw: !!window.__mcp_fetch_hooked,"
                " tick: !!window.__mcp_vm_loop_tick,"
                " xhr_calls: (window.__mcp_xhr_log || []).length,"
                " fetch_calls: (window.__mcp_fetch_log || []).length,"
                " has_worker: typeof Worker !== 'undefined',"
                " has_wasm: typeof WebAssembly !== 'undefined'})")))

            # iframe / Worker realm 普查
            probe("iframe_census", lambda: call("evaluate_js", expression=(
                "mw:() => Array.prototype.map.call("
                "document.querySelectorAll('iframe'), function(f){"
                " var tick; try { tick = !!(f.contentWindow && f.contentWindow.__mcp_vm_loop_tick); }"
                " catch(e) { tick = 'cross-origin'; }"
                " return {src: String(f.src||'').substring(0,120),"
                " sandbox: String(f.sandbox||''), tick: tick};})")))

            def drain_loops():
                result = call("vm_loop_trace", action="log")
                # 循环的完整 states 在 artifact 里；summary 只留元数据级字段，
                # 避免 148k 级快照把 summary.json 撑爆。
                loops_meta = [{k: l.get(k) for k in
                               ("loop", "realm", "iterations", "states_recorded",
                                "truncated", "coverage_pct")}
                              | {"has_times": bool(l.get("times"))}
                              for l in result.get("loops", [])]
                return {
                    "loops_observed": result.get("loops_observed"),
                    "total_iterations": result.get("total_iterations"),
                    "counters": result.get("counters"),
                    "artifact": result.get("artifact"),
                    "loops": loops_meta,
                }
            drain = probe("vm_loop_trace", drain_loops)

            # wasm hook 活性自检（第六阶段）：主世界手动实例化最小合法
            # 模块（8 字节 magic+version），再 drain 验证证据落账。
            # 用途：当目标有 wasm 请求但产物无 wasm_modules 时，把
            # 「浏览器盲区」与「目标在观测窗口内未实例化」区分开。
            def wasm_liveness():
                inst = call("evaluate_js", expression=(
                    "mw:() => WebAssembly.instantiate("
                    "new Uint8Array([0,97,115,109,1,0,0,0]), {})"
                    ".then(function(r){ return {ok: !!r.instance}; })"
                    ".catch(function(e){ return {err: String(e)}; })"),
                    await_promise=True)
                try:
                    call("evaluate_js",
                         expression="new Promise(r => setTimeout(r, 1500))",
                         await_promise=True)
                except Exception:
                    pass
                check_drain = call("vm_loop_trace", action="log")
                mods = check_drain.get("wasm_modules") or []
                return {"instantiate": inst,
                        "evidence": any(m.get("bytes_len") == 8
                                        for m in mods)}
            probe("wasm_hook_liveness", wasm_liveness)

            requests = call("list_network_requests")
            if isinstance(requests, dict):
                requests = requests.get("result", [requests])
            summary["requests_total"] = len(requests)
            (report_dir / "network-requests.json").write_text(
                json.dumps(requests, ensure_ascii=False, indent=2))

            probe("trace_files", lambda: call("list_trace_files"))

            # route 统计落盘（第十阶段切片 B）：stop 回传 route_hits/
            # route_errors/route_fetch_fallback/content_types，让 loopback
            # 绕行是否生效可观测（此前探针从不 stop，统计直接丢弃）。
            try:
                summary["route_stats"] = call("vm_loop_trace", action="stop")
            except Exception as error:
                summary["route_stats"] = {"error": str(error)[:200]}
        except Exception as error:
            summary["exception_type"] = type(error).__name__
            summary["exception"] = str(error)[:300]
        finally:
            if launched:
                try:
                    closed = call("close_browser")
                    summary["capture_status"] = closed.get("status")
                    capture = closed.get("capture", {})
                    summary["capture_errors"] = dict(Counter(
                        e.get("stage", "?") for e in capture.get("errors", [])))
                    summary["capture_stats"] = capture.get("stats")
                except Exception as error:
                    summary["close_exception_type"] = type(error).__name__
            (report_dir / "mcp-transcript.json").write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2))
            (report_dir / "server-stderr.log").write_text(client.server_stderr)

    if summary.get("session_dir"):
        session = Path(summary["session_dir"])
        trace_dir = session / "trace"
        summary["native_trace_files"] = len(list(trace_dir.rglob("*.jsonl"))) if trace_dir.exists() else 0
        summary["native_events"] = sum(sum(1 for _ in p.open()) for p in trace_dir.rglob("*.jsonl")) \
            if trace_dir.exists() else 0
        sidecars = list(trace_dir.rglob("*.meta.json")) if trace_dir.exists() else []
        summary["native_dropped"] = sum(json.loads(p.read_text()).get("dropped", -1) for p in sidecars)

        summary["scorecard"] = score_coverage(summary, session, report_dir)
        (session / "derived").mkdir(parents=True, exist_ok=True)
        (session / "derived" / "coverage-scorecard.json").write_text(
            json.dumps(summary["scorecard"], ensure_ascii=False, indent=2))

    passed = (summary.get("manifest_ok", True)
              and summary.get("native_events", 0) > 0
              and summary.get("native_dropped") == 0)
    summary["status"] = "passed" if passed else "failed"
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


def score_coverage(summary, session: Path, report_dir: Path) -> dict:
    """能力模型第 5 节的 10 条清单，通用部分自动打分，目标特定项如实标。

    每条：{"verdict": "pass"|"fail"|"unknown"|"needs-adapter", "detail": ...}
    unknown 是诚实答案（当前能力观测不到），fail 是能力缺口。
    """
    checks = {}
    drain = summary.get("vm_loop_trace") or {}
    loops = drain.get("loops") or []
    artifact_path = drain.get("artifact")

    # 1. 动态源码全文在产物里吗（iframe-create/worker-gap 等零长度标记
    #    是事件元数据不是源码，不计入分母；超 256KB 上限的源码若能在
    #    raw/scripts 网络捕获里按哈希命中，视为离线可用）
    sources = []
    wasm_mods = []
    value_taps = []
    trace_seq = []
    artifact_loops = []
    artifact = {}
    if artifact_path and Path(artifact_path).exists():
        artifact = json.loads(Path(artifact_path).read_text())
        sources = artifact.get("dynamic_sources") or []
        wasm_mods = artifact.get("wasm_modules") or []
        value_taps = artifact.get("value_taps") or []
        trace_seq = artifact.get("trace_seq") or []
        artifact_loops = artifact.get("loops") or []
    real = [s for s in sources if s.get("length", 0) > 0]
    full = [s for s in real if s.get("source")]
    oversized = [s for s in real if not s.get("source")]

    def _js_hash(text: str) -> str:
        # 与 hook 的 hashOf 一致：UTF-16 码元上的 31 倍滚动哈希
        h = 0
        data = text.encode("utf-16-le", "surrogatepass")
        for i in range(0, len(data), 2):
            h = (h * 31 + (data[i] | (data[i + 1] << 8))) & 0xFFFFFFFF
            if h >= 0x80000000:
                h -= 0x100000000  # JS |0 的 int32 语义
        return format(h & 0xFFFFFFFF, "x")

    cross = 0
    if oversized and (session / "raw" / "scripts").exists():
        script_texts = []
        for p in (session / "raw" / "scripts").glob("*.js"):
            try:
                script_texts.append(p.read_text(encoding="utf-8",
                                                errors="replace"))
            except Exception:
                continue
        for s in oversized:
            for text in script_texts:
                if len(text) == s.get("length") and _js_hash(text) == s.get("hash"):
                    cross += 1
                    break
    covered = len(full) + cross
    checks["1_dynamic_source_fulltext"] = {
        "verdict": "pass" if real and covered == len(real)
        else ("fail" if real else "unknown"),
        "detail": f"{len(full)}/{len(real)} 条动态源码含全文"
                  + (f"，{cross} 条超限源码在 raw/scripts 哈希命中"
                     if cross else "")
                  + f"（另有 {len(sources) - len(real)} 条零长度事件标记）"}

    # 2. 循环轨迹有截断吗
    truncated = [l for l in loops if l.get("truncated")]
    checks["2_loop_trace_complete"] = {
        "verdict": "pass" if loops and not truncated
        else ("fail" if truncated else "unknown"),
        "detail": f"{len(loops)} 循环，截断 {len(truncated)} 个，"
                  f"总迭代 {drain.get('total_iterations')}"}

    # 3/4. 派发形态裁决与 opcode 清单：目标特定分析，通用探针只给原料是否齐
    checks["3_dispatch_form"] = {
        "verdict": "needs-adapter",
        "detail": "原料（循环+状态+源码全文）在产物中；裁决需目标适配器"}
    # 第九阶段：有序列记录（适配器经 --code-patches 锚了 handler 并调
    # __mcp_vm_rec）即 pass；无补丁则原料齐、需适配器，如实 needs-adapter
    tags = Counter(e.get("tag") for e in trace_seq)
    checks["4_opcode_semantics"] = {
        "verdict": "pass" if trace_seq else "needs-adapter",
        "detail": (f"序列 {len(trace_seq)} 条，tag 分布 "
                   f"{dict(tags.most_common(8))}" if trace_seq else
                   "频次/序列通道已就绪（__mcp_vm_rec）；"
                   "锚点正则需目标适配器")}

    # 5. 加密参数候选字段定位
    requests = []
    req_file = report_dir / "network-requests.json"
    if req_file.exists():
        requests = json.loads(req_file.read_text())
    hits = []
    for r in requests:
        url = r.get("url", "").lower()
        for hint in ENCRYPTED_PARAM_HINTS:
            if hint in url:
                hits.append({"hint": hint, "url": r.get("url", "")[:160]})
                break
    checks["5_encrypted_param_candidates"] = {
        "verdict": "pass" if hits else "unknown",
        "detail": f"{len(hits)} 个请求含加密参数签名",
        "hits": hits[:10]}

    # 6. 值级因果关联（第七阶段 taint-lite）：候选加密参数值 ←→
    #    值变换事件（hook 9）按值相等匹配（哈希对哈希 / 预览前缀）。
    #    命中即「该请求字段值由这个 API 在这个 realm 这个时间产出」。
    from urllib.parse import parse_qsl
    candidates = []  # {param, value, where, url}
    for r in requests:
        try:
            for k, v in parse_qsl(urlsplit(r.get("url", "")).query):
                if 8 <= len(v) <= 512:
                    candidates.append({"param": k, "value": v,
                                       "where": "query",
                                       "url": r.get("url", "")[:120]})
        except Exception:
            continue
    # POST 体（urlencoded / JSON 两种形态，>200KB 跳过）
    if (session / "raw" / "network").exists():
        for p in session.rglob("raw/network/*/request.body"):
            try:
                text = p.read_bytes().decode("utf-8")
            except Exception:
                continue
            if not text or len(text) > 200000:
                continue
            pairs = []
            if "=" in text and not text.lstrip().startswith(("{", "[")):
                try:
                    pairs = parse_qsl(text)
                except Exception:
                    pairs = []
            else:
                try:
                    obj = json.loads(text)
                    if isinstance(obj, dict):
                        pairs = [(k, v) for k, v in obj.items()
                                 if isinstance(v, str)]
                except Exception:
                    pairs = []
            for k, v in pairs:
                if 8 <= len(v) <= 512:
                    candidates.append({"param": k, "value": v,
                                       "where": "post-body",
                                       "url": ""})
            if len(candidates) > 2000:
                break

    def _match_tap(cval):
        """tap-hash：哈希+长度精确；tap-preview：短值全等/长值 64 字符前缀。"""
        chash = _js_hash(cval)
        for t in value_taps:
            if (t.get("out_hash") == chash
                    and t.get("out_len") == len(cval)):
                return ("tap-hash", t)
            prev = t.get("out_preview") or ""
            if prev and cval[:len(prev)] == prev and \
                    (len(cval) <= 64 or len(prev) == 64):
                return ("tap-preview", t)
        return (None, None)

    # 第八阶段扩展证据：loop 状态快照里的字符串（去重，值 → 首个出处）。
    # VM 寄存器快照常含中间/最终字符串，命中即「值经过这个循环」。
    state_str_index = {}
    _STATES_SCAN_CAP = 20000  # 每循环扫描上限，控制离线耗时

    def _collect_strings(obj, out):
        if isinstance(obj, str):
            if 2 <= len(obj) <= 1024:
                out.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect_strings(v, out)
        elif isinstance(obj, list):
            for v in obj:
                _collect_strings(v, out)

    _buf = []
    for loop in artifact_loops:
        if len(state_str_index) >= 20000:
            break  # 全局上限：片段匹配是候选×索引的乘积，控制离线耗时
        states = loop.get("states") or []
        for entry in states[:_STATES_SCAN_CAP]:
            _buf.clear()
            _collect_strings(entry, _buf)
            for s in _buf:
                if s not in state_str_index:
                    state_str_index[s] = (loop.get("loop"), loop.get("realm"))

    linkages = []
    seen_vals = set()
    for c in candidates:
        if c["value"] in seen_vals:
            continue
        seen_vals.add(c["value"])
        via, t = _match_tap(c["value"])
        if t:
            linkages.append({
                "param": c["param"], "where": c["where"], "via": via,
                "api": t.get("api"), "realm": t.get("realm"),
                "ts": t.get("ts"), "url": c["url"]})
        elif c["value"] in state_str_index:
            lp, rl = state_str_index[c["value"]]
            linkages.append({
                "param": c["param"], "where": c["where"], "via": "state-exact",
                "loop": lp, "realm": rl, "url": c["url"]})
        else:
            # 片段匹配：值事件/快照里的 ≥4 字符片段被候选值包含
            # （fromCharCode 装配的签名末段典型形态）
            frag = None
            for t in value_taps:
                prev = t.get("out_preview") or ""
                if len(prev) >= 4 and prev in c["value"]:
                    frag = {"via": "fragment", "fragment": prev,
                            "api": t.get("api"), "realm": t.get("realm"),
                            "ts": t.get("ts")}
                    break
            if not frag:
                for s, (lp, rl) in state_str_index.items():
                    if len(s) >= 4 and (s in c["value"] or c["value"] in s):
                        frag = {"via": "state-fragment",
                                "fragment": s[:40], "loop": lp, "realm": rl}
                        break
            if frag:
                linkages.append({"param": c["param"], "where": c["where"],
                                 "url": c["url"], **frag})
        if len(linkages) >= 10:
            break
    checks["6_value_level_linkage"] = {
        "verdict": "pass" if linkages else "unknown",
        "detail": (f"{len(linkages)} 条值级关联命中（候选 {len(seen_vals)}，"
                   f"值事件 {len(value_taps)}，快照字符串 "
                   f"{len(state_str_index)}）" if linkages else
                   f"候选 {len(seen_vals)} 个无命中（值事件 "
                   f"{len(value_taps)} 条，快照字符串 "
                   f"{len(state_str_index)} 个）：变换未走包装清单内 API，"
                   f"或值在环形缓冲溢出"),
        "linkages": linkages}

    # 7. redirect 链自描述（network metadata 里 body_availability 分布）
    net_meta = list(session.rglob("raw/network/*/metadata.json")) \
        if (session / "raw" / "network").exists() else []
    availability = Counter()
    for p in net_meta:
        try:
            row = json.loads(p.read_text())
        except Exception:
            continue
        availability[row.get("body_availability", "ok")] += 1
    checks["7_redirect_chain_described"] = {
        "verdict": "pass" if net_meta else "unknown",
        "detail": f"{len(net_meta)} 请求，body_availability 分布 "
                  f"{dict(availability)}"}

    # 8. 原生事件零丢失
    dropped = summary.get("native_dropped", -1)
    checks["8_native_events_no_loss"] = {
        "verdict": "pass" if dropped == 0 else "fail",
        "detail": f"native_events={summary.get('native_events')}, dropped={dropped}"}

    # 9. realm 使用与覆盖（Worker/iframe/wasm）
    census = summary.get("iframe_census") or []
    if isinstance(census, dict) and isinstance(census.get("value"), list):
        census = census["value"]  # evaluate_js 的 {"type","value"} 包壳
    if not isinstance(census, list):
        census = []
    census = [f for f in census if isinstance(f, dict)]
    cross = [f for f in census if f.get("tick") == "cross-origin"]
    no_tick = [f for f in census if f.get("tick") is False]
    worker_gaps = [s for s in sources if s.get("kind") == "worker-gap"]
    sw_gaps = [s for s in sources if s.get("kind") == "serviceworker-gap"]
    # 第十一阶段：worker-gap 不等于未覆盖——页面内同步 XHR 拿不到源码
    # 的 Worker（CSP connect-src 拦截等），其真实网络请求会过 context
    # route，route 层对 worker JS 响应前置了完整运行时。URL 前缀命中
    # route stats 的 worker_urls 即视为已经 route 插桩覆盖。
    route_worker_urls = []
    for _rm in (summary.get("route_stats") or {}).get("removed") or []:
        _st = (_rm or {}).get("stats") or {}
        route_worker_urls.extend(_st.get("worker_urls") or [])
    def _route_covered(gap):
        # worker-gap 条目把 URL 存在 src 槽位（source 全文 / prefix 前 160 字符）
        gurl = str(gap.get("source") or gap.get("prefix") or "").split(" :: ")[0]
        return any(gurl.startswith(wu) or wu.startswith(gurl)
                   for wu in route_worker_urls if wu)
    def _fetched_ok(gap):
        f = gap.get("fetched") or {}
        return isinstance(f, dict) and not f.get("error") and \
            (f.get("file") or f.get("source"))
    worker_blocked = [s for s in sources if s.get("kind") == "worker-blocked"]
    worker_route = [g for g in worker_gaps if _route_covered(g)]
    # 取回覆盖：context.request 已把脚本落盘（file）或内嵌（source），
    # 离线可用即不算缺口（worker-blocked 同理——worker 未执行、无运行
    # 时事件可丢，源码取回后产物对离线分析完整）
    worker_fetched = [g for g in worker_gaps
                      if not _route_covered(g) and _fetched_ok(g)]
    blocked_fetched = [g for g in worker_blocked if _fetched_ok(g)]
    worker_uncovered = [g for g in worker_gaps
                        if not _route_covered(g) and not _fetched_ok(g)]
    blocked_uncovered = [g for g in worker_blocked if not _fetched_ok(g)]
    # 第十阶段切片 D：跨域 frame 经 postMessage 桥聚合后不算缺口——
    # drain 产物 realms 里出现 frame[i]/ 且有数据即视为已覆盖
    bridged = {m.group(1) for r in (artifact.get("realms") or [])
               for m in [re.match(r"frame\[(\d+)\]/", r.get("realm") or "")]
               if m and not r.get("error")}
    cross_uncovered = max(0, len(cross) - len(bridged))
    wasm_req = [r for r in requests if ".wasm" in r.get("url", "").lower()]
    realm_detail = (
        f"iframe {len(census)}（跨域 {len(cross)}：已桥聚合 {len(bridged)}、"
        f"未覆盖 {cross_uncovered}，未装运行时 {len(no_tick)}），"
        f"worker-gap {len(worker_gaps)}（route 插桩 {len(worker_route)}、"
        f"已取回 {len(worker_fetched)}、未覆盖 {len(worker_uncovered)}），"
        f"worker-blocked {len(worker_blocked)}（已取回 {len(blocked_fetched)}），"
        f"serviceworker-gap {len(sw_gaps)}，"
        f"wasm 请求 {len(wasm_req)}")
    verdict = "pass"
    if cross_uncovered or no_tick or worker_uncovered or blocked_uncovered:
        verdict = "fail"
    # 第六阶段：wasm 诚实判定 —— 目标加载了 wasm 时，drain 产物必须有
    # wasm_modules 证据（哈希/exports/调用计数）；再把 drain 哈希与 raw
    # 网络捕获字节做 31 滚动哈希交叉（与 hook 的 hashBytes 同款），
    # 命中的模块即离线可用（即使超 768KB 未内嵌 base64 也能取回字节）。
    if wasm_req:
        def _bytes_hash(data: bytes) -> str:
            h = 0
            for b in data:
                h = (h * 31 + b) & 0xFFFFFFFF
                if h >= 0x80000000:
                    h -= 0x100000000  # JS |0 的 int32 语义
            return format(h & 0xFFFFFFFF, "x")

        wasm_hashes = {m.get("hash") for m in wasm_mods}
        cross_hits = 0
        if wasm_hashes and (session / "raw" / "network").exists():
            for p in session.rglob("raw/network/*/response.body"):
                try:
                    if _bytes_hash(p.read_bytes()) in wasm_hashes:
                        cross_hits += 1
                except Exception:
                    continue
        realm_detail += (f"，wasm 模块证据 {len(wasm_mods)} 条"
                         f"（{len(wasm_hashes)} 哈希），"
                         f"raw 网络交叉命中 {cross_hits}")
        if not wasm_mods:
            liveness = summary.get("wasm_hook_liveness") or {}
            if liveness.get("evidence"):
                # hook 在线但目标未实例化：非浏览器缺口，如实记 unknown
                realm_detail += "，hook 活性已验证（观测窗口内目标未实例化）"
                if verdict != "fail":
                    verdict = "unknown"
            else:
                realm_detail += "，hook 活性未验证：仍是盲区"
                verdict = "fail"
    checks["9_realm_coverage"] = {"verdict": verdict, "detail": realm_detail}

    # 10. 时序对齐字段在位（tick_times 开了，循环应带 times）
    with_times = [l for l in loops if l.get("has_times")]
    checks["10_timing_alignment"] = {
        "verdict": "pass" if loops and with_times
        else ("unknown" if not loops else "fail"),
        "detail": f"{len(with_times)}/{len(loops)} 循环带 times"}

    passed_n = sum(1 for c in checks.values() if c["verdict"] == "pass")
    return {"checks": checks, "pass": passed_n, "total": len(checks),
            "note": "needs-adapter=原料齐、需目标适配器；unknown=能力观测不到，如实登记"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--proxy", default="",
                        help="留空直连；Google 系目标用 http://127.0.0.1:7890")
    parser.add_argument("--browser-version", default=BROWSER_SELECTOR)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--wait-seconds", type=int, default=12,
                        help="页面加载后等 VM 运行的秒数")
    parser.add_argument("--wait-until", default="domcontentloaded",
                        choices=["load", "domcontentloaded", "networkidle"],
                        help="导航完成判定；重站点（视频流）用 domcontentloaded")
    parser.add_argument("--click-expression", default="",
                        help="可选：初始等待后在主世界执行的 JS 表达式（如点击 "
                             "演示复选框触发 VM），仅用于无害交互")
    parser.add_argument("--post-click-wait-seconds", type=int, default=10)
    parser.add_argument("--code-patches", default="",
                        help="可选：JSON 字符串或 @文件路径，目标适配器锚点"
                             "补丁（name/pattern/replacement），透传 "
                             "vm_loop_trace install；补丁内可调 "
                             "__mcp_vm_rec(tag, vals) 记录序列")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
