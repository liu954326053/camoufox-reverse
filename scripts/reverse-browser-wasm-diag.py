#!/usr/bin/env python3
"""wasm hook 现场诊断（第六阶段 Task 3）。

抖音复测 drain 无 wasm_modules，但 pylon-wasm 的 1.17MB 响应体已被网络
捕获。本脚本回答三个问题：
1. 主世界的 WebAssembly 四入口是否被 hook 8 包装（函数源码判定）；
2. 在主世界手动 instantiate 一个最小 wasm，drain 是否落证据（活性验证）；
3. 页面所有 iframe realm 的运行时/ wasm 证据分布（定位盲区 realm）。
"""

import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "pythonlib"))
sys.path.insert(0, str(ROOT))
from camoufox_reverse_mcp_client import MCPStdioClient
from camoufox.reverse_compat import BROWSER_SELECTOR

TARGET = sys.argv[1] if len(sys.argv) > 1 else "https://www.douyin.com/"
OUT = ROOT / "artifacts/analysis/phase6/wasm-diag" / uuid.uuid4().hex[:8]
OUT.mkdir(parents=True, exist_ok=True)


def unpack(reply):
    values = [json.loads(item["text"]) for item in reply.get("content", [])
              if item.get("type") == "text"]
    value = values[0] if len(values) == 1 else values
    if reply.get("isError") or isinstance(value, dict) and value.get("error"):
        raise RuntimeError(
            f"MCP tool failed: {json.dumps(value, ensure_ascii=False)[:400]}")
    return value


def main():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "pythonlib"), str(ROOT / "integrations/camoufox-reverse-mcp/src"),
         environment.get("PYTHONPATH", "")])
    server_args = ["-m", "camoufox_reverse_mcp", "--project-dir", str(OUT),
                   "--headless", "--os", "macos", "--locale", "en-US"]
    out = {}
    with MCPStdioClient(sys.executable, args=server_args,
                        env=environment, timeout=120) as client:
        client.initialize()

        def call(name, **arguments):
            return unpack(client.call_tool(name, arguments))

        call("launch_browser", project_dir=str(OUT),
             browser_version=BROWSER_SELECTOR, headless=True,
             os_type="macos", locale="en-US", enable_trace=False,
             capture_profile="raw")
        call("vm_loop_trace", action="install", url_pattern="**",
             tick_times=True, max_states_per_loop=1000)
        try:
            call("navigate", url=TARGET, wait_until="domcontentloaded")
        except Exception:
            pass
        # 分段容忍等待：目标重定向会销毁执行上下文
        waited = 0.0
        while waited < 12:
            step = min(2.0, 12 - waited)
            try:
                call("evaluate_js",
                     expression=f"new Promise(r => setTimeout(r, {int(step * 1000)}))",
                     await_promise=True)
            except Exception:
                pass
            waited += step

        def probe(name, fn):
            try:
                out[name] = fn()
            except Exception as error:
                out[name] = {"probe_error": f"{type(error).__name__}: {error}"[:300]}

        # 1. 主世界四入口包装状态（原生函数 toString 含 [native code]）
        probe("hook_state", lambda: call("evaluate_js", expression=(
            "mw:() => ({compile: String(WebAssembly.compile).substring(0,60),"
            " instantiate: String(WebAssembly.instantiate).substring(0,60),"
            " compileStreaming: String(WebAssembly.compileStreaming).substring(0,60),"
            " instantiateStreaming: String(WebAssembly.instantiateStreaming).substring(0,60),"
            " tick: !!window.__mcp_vm_loop_tick})")))

        # 2. 活性验证：主世界手动实例化最小合法 wasm（magic+version 空模块）
        probe("manual_instantiate", lambda: call("evaluate_js", expression=(
            "mw:() => WebAssembly.instantiate("
            "new Uint8Array([0,97,115,109,1,0,0,0]), {})"
            ".then(function(r){ return {ok: !!r.instance}; })"
            ".catch(function(e){ return {err: String(e)}; })"),
            await_promise=True))

        # 2b. 带导出函数的 36 字节模块：exports 包装/计数路径活性
        probe("manual_instantiate_exports", lambda: call("evaluate_js", expression=(
            "mw:() => WebAssembly.instantiate(new Uint8Array(["
            "0,97,115,109,1,0,0,0,"
            "1,5,1,96,0,1,127,"
            "3,2,1,0,"
            "7,7,1,3,114,117,110,0,0,"
            "10,6,1,4,0,65,7,11]), {})"
            ".then(function(r){"
            "  var v = r.instance.exports.run();"
            "  return {ok: v === 7,"
            "    frozen: Object.isFrozen(r.instance.exports)};"
            "}).catch(function(e){ return {err: String(e)}; })"),
            await_promise=True))

        # 2c. 页面自身 wasm 调用结果（靶场页面全局标志）
        probe("page_wasm_results", lambda: call("evaluate_js", expression=(
            "mw:() => ({streaming: window.__streaming_result,"
            " module: window.__module_result,"
            " bytes: window.__bytes_result,"
            " cs: window.__compile_streaming_done})")))
        try:
            call("evaluate_js",
                 expression="new Promise(r => setTimeout(r, 1500))",
                 await_promise=True)
        except Exception:
            pass

        # 3. iframe realm 普查 + 各 realm 运行时/证据
        probe("iframe_census", lambda: call("evaluate_js", expression=(
            "mw:() => Array.prototype.map.call("
            "document.querySelectorAll('iframe'), function(f){"
            " var st; try {"
            "  st = {tick: !!(f.contentWindow && f.contentWindow.__mcp_vm_loop_tick),"
            "        wasm: !!(f.contentWindow && f.contentWindow.__mcp_vm_loop_state),"
            "        href: String(f.contentWindow.location.href).substring(0,100)};"
            " } catch(e) { st = 'cross-origin'; }"
            " return {src: String(f.src||'').substring(0,120), state: st};})")))

        drain = call("vm_loop_trace", action="log")
        out["drain"] = {k: drain.get(k) for k in
                        ("loops_observed", "total_iterations", "wasm_modules",
                         "counters")}
        art = drain.get("artifact")
        if art:
            data = json.loads(Path(art).read_text())
            out["realms"] = [r.get("realm") for r in data.get("realms", [])]
            out["wasm_modules_full"] = data.get("wasm_modules")
        call("close_browser")

    (OUT / "diag.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("diag:", OUT / "diag.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
