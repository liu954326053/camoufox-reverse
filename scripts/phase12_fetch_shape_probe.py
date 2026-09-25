"""第十二阶段：测量 route.fetch() 重发请求与浏览器原始请求的头部差异。

背景：leboncoin 实测中，插桩会话（route.fetch 重发）对 authorize 端点
稳定拿到 200 陈旧预渲染 HTML，被动会话（浏览器直发）稳定 302/403。
需确认 route.fetch 重发请求的头部形态与浏览器原始请求差在哪。

用法：/usr/local/bin/python3.12 scripts/phase12_fetch_shape_probe.py <label>
  label 含 passive → 不装 route（浏览器直发对照）
"""
import json, os, sys, time

ROOT = "/Users/magic/Workspace/camoufox-reverse"
env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join([ROOT + "/pythonlib", ROOT + "/integrations/camoufox-reverse-mcp/src"])
sys.path.insert(0, ROOT + "/integrations/camoufox-reverse-mcp/src")
sys.path.insert(0, ROOT + "/pythonlib")
from camoufox_reverse_mcp_client import MCPStdioClient

def unpack(reply):
    values = [json.loads(i["text"]) for i in reply.get("content", []) if i.get("type") == "text"]
    v = values[0] if len(values) == 1 else values
    if reply.get("isError") or (isinstance(v, dict) and v.get("error")):
        raise RuntimeError(json.dumps(v, ensure_ascii=False)[:300])
    return v

label = sys.argv[1] if len(sys.argv) > 1 else "instr"
project = f"/tmp/phase12-shape-{label}"
os.makedirs(project, exist_ok=True)

with MCPStdioClient("/usr/local/bin/python3.12",
                    args=["-m", "camoufox_reverse_mcp", "--project-dir", project,
                          "--headless", "--proxy", ""],
                    env=env, timeout=120) as c:
    c.initialize()
    def call(n, **a): return unpack(c.call_tool(n, a))
    call("launch_browser", project_dir=project, headless=True, os_type="windows",
         locale="en-US", enable_trace=False, capture_profile="raw")
    if "passive" not in label:
        call("vm_loop_trace", action="install", url_pattern="**")
    try:
        call("navigate", url="https://httpbin.org/headers?probe=phase12",
             wait_until="load")
    except Exception as e:
        print("navigate:", str(e)[:120])
    time.sleep(3)
    try:
        st = call("evaluate_js", expression="document.body.innerText")
        t = st.get("value") if isinstance(st, dict) else st
        if isinstance(t, dict): t = t.get("value")
        print(f"[{label}] echoed headers:")
        print(t)
    except Exception as e:
        print("eval err", str(e)[:100])
    try: call("close_browser")
    except Exception: pass
