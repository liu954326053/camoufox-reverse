"""Offline tests for the generic interpreter-loop tracer (Task 2).

The loop rewriter must work on any while(true) dispatch loop without
knowing anything about a specific VM's semantics.
"""
from __future__ import annotations

from camoufox_reverse_mcp.utils.loop_rewriter import inject_loop_ticks, rewrite_inline_scripts

INTERPRETER = """
function run(program) {
    var N = 41, A = 88, acc = 0;
    while (true) {
        if (N == 81) break;
        else if (N == 41) { acc += program[0]; N = 7; }
        else if (N == 7) { A = acc * 2; N = 81; }
    }
    return acc + A;
}
"""

PLAIN = """
function add(a, b) {
    var sum = a + b;
    return sum;
}
"""


def test_tick_injected_at_loop_head():
    out, stats = inject_loop_ticks(INTERPRETER, state_vars=["N", "A"])
    assert out is not None
    assert stats["loops"] == 1
    assert "__mcp_vm_loop_tick" in out
    # tick 在循环体最前面
    head = out.index("while (true)")
    tick = out.index("__mcp_vm_loop_tick", head)
    assert tick < out.index("if (N == 81)", head)


def test_state_vars_captured_with_typeof_guard():
    out, _ = inject_loop_ticks(INTERPRETER, state_vars=["N", "A"])
    assert "typeof N" in out and "typeof A" in out


def test_loop_id_is_stable_source_offset():
    out, stats = inject_loop_ticks(INTERPRETER)
    first = INTERPRETER.index("while (true)")
    assert f'"L{first}"' in out


def test_no_loop_returns_source_unchanged():
    out, stats = inject_loop_ticks(PLAIN)
    assert stats["loops"] == 0
    assert out is not None and "__mcp_vm_loop_tick" not in out


def test_rewritten_source_still_parses():
    import esprima
    out, _ = inject_loop_ticks(INTERPRETER, state_vars=["N", "A"])
    esprima.parseScript(out)


def test_parse_failure_returns_none():
    out, stats = inject_loop_ticks("function {{{")
    assert out is None
    assert stats["parsed"] is False


def test_nested_loops_all_instrumented():
    src = "while(true){while(true){break;}break;}"
    out, stats = inject_loop_ticks(src)
    assert stats["loops"] == 2
    assert out.count("__mcp_vm_loop_tick(") == 2  # 只数调用点，不含守卫


def test_non_true_while_untouched():
    src = "var i=0; while (i < 10) { i++; }"
    out, stats = inject_loop_ticks(src)
    assert stats["loops"] == 0
    assert out == src


def test_non_block_body_wrapped():
    """BotGuard 风格：while(true)try{...} 非块循环体必须包成块再注入。"""
    import esprima
    src = "var N=41;while(true)try{if(N==81)break;else{N=81;}}catch(e){break;}"
    out, stats = inject_loop_ticks(src, state_vars=["N"])
    assert stats["loops"] == 1
    assert "__mcp_vm_loop_tick(" in out
    esprima.parseScript(out)  # 注入后语法必须仍然合法
    # tick 在 try 之前、循环体内部
    tick = out.index("__mcp_vm_loop_tick(")
    assert out.index("while(true)") < tick < out.index("try{")


HTML = """<html><head><script src="https://cdn.example.com/ext.js"></script>
<script nonce="abc123">function run(){var N=41;while(true){if(N==81)break;N=81;}}</script>
<script>var x = 1;</script>
</head><body>text</body></html>"""


def test_html_inline_loop_script_rewritten():
    out, stats = rewrite_inline_scripts(HTML, state_vars=["N"])
    assert stats["scripts_rewritten"] == 1
    assert "__mcp_vm_loop_tick" in out
    # nonce 保留、无循环脚本与外链脚本不动
    assert '<script nonce="abc123">' in out
    assert 'src="https://cdn.example.com/ext.js"' in out
    assert "var x = 1;" in out


def test_html_without_loops_unchanged():
    html = "<html><script>var a = 1;</script></html>"
    out, stats = rewrite_inline_scripts(html)
    assert stats["scripts_rewritten"] == 0
    assert out == html


# ============= vm_loop_trace 工具级测试（mock page，无浏览器） =============

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from camoufox_reverse_mcp.tools.vm_loop import vm_loop_trace


@pytest.mark.asyncio
async def test_install_requires_url_pattern():
    r = await vm_loop_trace(action="install")
    assert "error" in r and "url_pattern" in r["error"]


def test_runtime_template_fully_substituted_and_parses():
    """渲染后的运行时不能残留模板占位符，且必须是合法 JS。"""
    import re
    import esprima
    from camoufox_reverse_mcp.tools.vm_loop import _runtime_js

    out = _runtime_js(500, state_vars=["N", "A"], src_filter="signin",
                      max_sources=50)
    assert "{{" not in out and "}}" not in re.sub(r"\}\}", "", out)
    assert re.search(r"\{\{[A-Z_]+\}\}", out) is None
    assert "var MAX_STATES = 500;" in out
    assert 'var STATE_VARS = ["N", "A"];' in out
    assert 'var SRC_FILTER = "signin";' in out
    assert "var MAX_SOURCES = 50;" in out
    esprima.parseScript(out)


def test_runtime_template_defaults():
    import esprima
    from camoufox_reverse_mcp.tools.vm_loop import _runtime_js

    out = _runtime_js(1000)
    assert "var MAX_STATES = 1000;" in out
    assert "var STATE_VARS = [];" in out
    assert 'var SRC_FILTER = "";' in out
    assert "var TICK_TIMES = false;" in out  # 默认关：热循环无额外开销
    esprima.parseScript(out)


def test_runtime_template_tick_times_on():
    import esprima
    from camoufox_reverse_mcp.tools.vm_loop import _runtime_js

    out = _runtime_js(1000, tick_times=True)
    assert "var TICK_TIMES = true;" in out
    assert "{{TICK_TIMES}}" not in out
    esprima.parseScript(out)


@pytest.mark.asyncio
async def test_unknown_action_rejected():
    r = await vm_loop_trace(action="bogus")
    assert "error" in r


@pytest.mark.asyncio
async def test_log_without_runtime_returns_error():
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=None)
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await vm_loop_trace(action="log")
    assert "error" in r and "main world" in r["error"]
    (expr,), _ = mock_page.evaluate.call_args
    assert expr.startswith("mw:")  # 必须走主世界


@pytest.mark.asyncio
async def test_log_drain_shape():
    import json as _json
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps({
        "loops": [{"loop": "L9", "iterations": 12, "states_recorded": 12,
                   "truncated": False, "states": [{"N": 41}, {"N": 7}]}],
        "overhead": {"tick_calls": 12, "tick_overhead_ns": 4800, "per_tick_ns": 400},
    }))
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = None
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    assert r["loops_observed"] == 1
    assert r["total_iterations"] == 12
    assert r["overhead"]["per_tick_ns"] == 400
    assert r["artifact"] is None
    # Phase-4 Task 2: 覆盖率自描述（未截断循环 100%）
    assert r["loops"][0]["coverage_pct"] == 100.0


@pytest.mark.asyncio
async def test_log_drain_coverage_pct_truncated():
    """截断循环的覆盖率必须体现在 drain 输出里（第四阶段 Task 2）。"""
    import json as _json
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps({
        "loops": [{"loop": "Lhot", "iterations": 148910, "states_recorded": 10000,
                   "truncated": True, "states": []},
                  {"loop": "Lnovar", "iterations": 89550, "states_recorded": 0,
                   "truncated": False, "states": []},
                  {"loop": "Lcold", "iterations": 50, "states_recorded": 50,
                   "truncated": False, "states": []}],
        "overhead": {"tick_calls": 0, "tick_overhead_ns": 0, "per_tick_ns": 0},
    }))
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = None
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    by_id = {l["loop"]: l for l in r["loops"]}
    assert by_id["Lhot"]["coverage_pct"] == 6.7   # 10000/148910
    assert by_id["Lcold"]["coverage_pct"] == 100.0
    assert by_id["Lnovar"]["coverage_pct"] is None  # 无快照变量，纯计数 n/a


@pytest.mark.asyncio
async def test_log_drain_flattens_nested_worker_realms():
    """Worker 桥回传的嵌套 realm 数组被递归展平（第四阶段 Task 1）。"""
    import json as _json
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps([
        {"realm": "top", "data": {"loops": [], "dynamic_sources": [],
                                  "overhead": {}}},
        {"realm": "worker[0]", "data": [
            {"realm": "top", "data": {
                "loops": [{"loop": "Lw", "iterations": 3, "states_recorded": 3,
                           "truncated": False, "states": [[1], [2], [3]]}],
                "dynamic_sources": [], "overhead": {}}},
            {"realm": "worker[0]", "data": [
                {"realm": "top", "data": {
                    "loops": [{"loop": "Lww", "iterations": 2,
                               "states_recorded": 2, "truncated": False,
                               "states": [[1], [9]]}],
                    "dynamic_sources": [], "overhead": {}}}]}]},
    ]))
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = None
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    realms = {l["realm"] for l in r["loops"]}
    assert "worker[0]/top" in realms
    assert "worker[0]/worker[0]/top" in realms


@pytest.mark.asyncio
async def test_log_drain_merges_wasm_modules_across_realms():
    """wasm 模块证据跨 realm 汇总并打 realm 标签（第六阶段 Task 2）。"""
    import json as _json
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps([
        {"realm": "top", "data": {
            "loops": [], "dynamic_sources": [], "overhead": {},
            "wasm_modules": [
                {"hash": "a1b2", "bytes_len": 8, "kind": "instantiate",
                 "exports": ["run"]}]},
        },
        {"realm": "worker[0]", "data": [
            {"realm": "top", "data": {
                "loops": [], "dynamic_sources": [], "overhead": {},
                "wasm_modules": [
                    {"hash": "c3d4", "bytes_len": 16,
                     "kind": "instantiateStreaming", "prov_id": "s1"}]}},
        ]},
    ]))
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = None
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    mods = r["wasm_modules"]
    assert len(mods) == 2
    by_hash = {m["hash"]: m for m in mods}
    assert by_hash["a1b2"]["realm"] == "top"
    assert by_hash["c3d4"]["realm"] == "worker[0]/top"
    assert by_hash["c3d4"]["prov_id"] == "s1"


@pytest.mark.asyncio
async def test_log_drain_merges_value_taps_across_realms():
    """值变换事件跨 realm 汇总、打 realm 标签并按 ts 排序（第七阶段 Task 2）。"""
    import json as _json
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps([
        {"realm": "top", "data": {
            "loops": [], "dynamic_sources": [], "overhead": {},
            "value_taps": [
                {"api": "btoa", "ts": 200, "in_len": 3, "in_hash": "a",
                 "in_preview": "abc", "out_len": 4, "out_hash": "b",
                 "out_preview": "YWJj"}]},
        },
        {"realm": "worker[0]", "data": [
            {"realm": "top", "data": {
                "loops": [], "dynamic_sources": [], "overhead": {},
                "value_taps": [
                    {"api": "crypto.subtle.digest", "ts": 100, "in_len": 8,
                     "in_hash": "c", "in_preview": "0011", "out_len": 32,
                     "out_hash": "d", "out_preview": "ff00"}]}},
        ]},
    ]))
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = None
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    taps = r["value_taps"]
    assert len(taps) == 2
    # ts 升序：worker 的 digest(ts=100) 排在 top 的 btoa(ts=200) 前
    assert taps[0]["api"] == "crypto.subtle.digest"
    assert taps[0]["realm"] == "worker[0]/top"
    assert taps[1]["realm"] == "top"


@pytest.mark.asyncio
async def test_log_drain_merges_trace_seq_across_realms():
    """opcode 序列跨 realm 合并：ts 升序、realm 标签、overflow 求和
    （第九阶段 Task 2）。"""
    import json as _json
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps([
        {"realm": "top", "data": {
            "loops": [], "dynamic_sources": [], "overhead": {},
            "trace_seq": [
                {"seq": 2, "tag": "Du", "ts": 200,
                 "vals": [{"len": 3, "hash": "x", "preview": "232"}]}],
            "trace_seq_overflow": 3},
        },
        {"realm": "worker[0]", "data": [
            {"realm": "top", "data": {
                "loops": [], "dynamic_sources": [], "overhead": {},
                "trace_seq": [
                    {"seq": 1, "tag": "Du", "ts": 100,
                     "vals": [{"len": 3, "hash": "y", "preview": "140"}]}],
                "trace_seq_overflow": 2}},
        ]},
    ]))
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = None
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    seq = r["trace_seq"]
    assert len(seq) == 2
    assert seq[0]["ts"] == 100 and seq[0]["realm"] == "worker[0]/top"
    assert seq[1]["realm"] == "top"


@pytest.mark.asyncio
async def test_log_drain_streams_states_when_payload_huge():
    """第十阶段切片 E1：payload 超 32MB 时 loops[].states 拆旁车 NDJSON，
    主 JSON 留元数据 + states_file 指针。"""
    import json as _json
    from pathlib import Path as _Path
    big = "x" * 1024  # 1KB/条 × 34000 条 ≈ 34MB
    states = [{"N": big} for _ in range(34000)]
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps({
        "loops": [{"loop": "Lbig", "iterations": 34000,
                   "states_recorded": 34000, "truncated": False,
                   "states": states}],
        "overhead": {"tick_calls": 34000, "tick_overhead_ns": 0,
                     "per_tick_ns": 0},
    }))
    writes = []

    def _fake_write(path, data, kind, default=None):
        writes.append({"kind": kind, "data": bytes(data), "default": default})
        return _Path(default or "raw/vm-loop/x.json")

    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = object()  # 非 None 才落盘
        mock_bm.write_artifact = MagicMock(side_effect=_fake_write)
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    kinds = [w["kind"] for w in writes]
    assert kinds == ["vm-loop-states", "vm-loop-trace"]
    # 旁车 NDJSON：每行 {loop, realm, i, state}
    lines = writes[0]["data"].decode().strip().split("\n")
    assert len(lines) == 34000
    row = _json.loads(lines[0])
    assert row["loop"] == "Lbig" and row["i"] == 0 and row["state"] == {"N": big}
    # 主 JSON：states 被指针替换，states_streamed 自描述
    main = _json.loads(writes[1]["data"].decode())
    mloop = main["loops"][0]
    assert "states" not in mloop
    assert mloop["states_file"].endswith("-states.ndjson")
    assert main["states_streamed"]["states"] == 34000
    assert len(writes[1]["data"]) < 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_log_drain_small_payload_not_split():
    """切片 E1：未超阈值保持单文件形态（不拆旁车）。"""
    import json as _json
    from pathlib import Path as _Path
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=_json.dumps({
        "loops": [{"loop": "Ls", "iterations": 2, "states_recorded": 2,
                   "truncated": False, "states": [{"N": 1}, {"N": 2}]}],
        "overhead": {},
    }))
    writes = []

    def _fake_write(path, data, kind, default=None):
        writes.append({"kind": kind, "data": bytes(data)})
        return _Path(default or "raw/vm-loop/y.json")

    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        mock_bm.session = object()
        mock_bm.write_artifact = MagicMock(side_effect=_fake_write)
        r = await vm_loop_trace(action="log")
    assert r["status"] == "ok"
    assert [w["kind"] for w in writes] == ["vm-loop-trace"]
    main = _json.loads(writes[0]["data"].decode())
    assert main["loops"][0]["states"] == [{"N": 1}, {"N": 2}]
    assert "states_streamed" not in main


# ---- 第五阶段 Task 3：外部 script src 插桩 + 无限循环形态扩展 ----

JSVMP_WHILE_1 = """
function vm(p){var s=0;while(1){if(s==2){break;}s=p[s];}return s;}
"""

JSVMP_FOR_EMPTY = """
function vm(p){for(;;){if(p[0]==9)return 1;return 2;}}
"""


def test_while_literal_1_instrumented():
    """JSVMP 常见 while(1) 数字字面量形态也要插桩。"""
    out, stats = inject_loop_ticks(JSVMP_WHILE_1)
    assert stats["loops"] == 1
    assert "__mcp_vm_loop_tick" in out


def test_for_empty_test_instrumented():
    """for(;;) 无条件表达式形态插桩。"""
    out, stats = inject_loop_ticks(JSVMP_FOR_EMPTY)
    assert stats["loops"] == 1
    assert "__mcp_vm_loop_tick" in out


def test_bounded_loop_not_instrumented():
    """有界循环（while(i<3) / for(i=0;i<3;i++)）不得误插桩。"""
    src = "function f(){var i=0;while(i<3){i++;}for(var j=0;j<3;j++){j;}return i;}"
    out, stats = inject_loop_ticks(src)
    assert stats["loops"] == 0
    assert out == src


def test_rewrite_js_body_by_content_type():
    from camoufox_reverse_mcp.tools.vm_loop import rewrite_js_body
    body = JSVMP_WHILE_1.encode()
    out, stats = rewrite_js_body(body, "application/javascript; charset=utf-8",
                                 url="https://x.example/sdk.js")
    assert stats["loops"] == 1
    assert b"__mcp_vm_loop_tick" in out


def test_rewrite_js_body_by_url_suffix():
    from camoufox_reverse_mcp.tools.vm_loop import rewrite_js_body
    body = JSVMP_WHILE_1.encode()
    out, stats = rewrite_js_body(body, "application/octet-stream",
                                 url="https://x.example/a/b.js?x=1")
    assert stats["loops"] == 1


def test_rewrite_js_body_skips_non_js():
    from camoufox_reverse_mcp.tools.vm_loop import rewrite_js_body
    out, stats = rewrite_js_body(b"{}", "application/json",
                                 url="https://x.example/api")
    assert out is None and stats.get("skipped")


def test_rewrite_js_body_parse_failure_returns_original():
    from camoufox_reverse_mcp.tools.vm_loop import rewrite_js_body
    body = b"export const x = 1; import y from 'z';"  # module 语法
    out, stats = rewrite_js_body(body, "application/javascript",
                                 url="https://x.example/m.js")
    assert out == body  # 解析失败不破坏原文件
