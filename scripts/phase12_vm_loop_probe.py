"""第十二阶段：leboncoin.fr（DataDome）vm_loop 插桩会话复测探针。

目的：验证 phase12 插桩会话 16×404（auth 子域 _next chunks）是否复现，
并取 route stats（route_hits / parse_failures / route_errors）对照。
用法：/usr/local/bin/python3.12 scripts/phase12_vm_loop_probe.py <label>
"""
import json, os, sys, time
from collections import Counter

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

label = sys.argv[1] if len(sys.argv) > 1 else "rerun"
project = f"/tmp/phase12-vmloop-{label}"
os.makedirs(project, exist_ok=True)

with MCPStdioClient("/usr/local/bin/python3.12",
                    args=["-m", "camoufox_reverse_mcp", "--project-dir", project,
                          "--headless", "--proxy", "socks5://127.0.0.1:7890"],
                    env=env, timeout=180) as c:
    c.initialize()
    def call(n, **a): return unpack(c.call_tool(n, a))
    call("launch_browser", project_dir=project, headless=True, os_type="windows",
         locale="en-US", enable_trace=False, capture_profile="raw")
    call("network_capture", action="start", capture_body=False)
    # 与 phase12 插桩会话同参数：全量 route 插桩；label 含 passive 时跳过
    if "passive" not in label:
        call("vm_loop_trace", action="install", url_pattern="**")
    try:
        call("navigate", url="https://www.leboncoin.fr/", wait_until="load")
    except Exception as e:
        print("navigate:", str(e)[:120])
    for i in range(9):
        time.sleep(3)
        try:
            st = call("evaluate_js", expression="document.title")
            t = st.get("value") if isinstance(st, dict) else st
            if isinstance(t, dict): t = t.get("value")
            print(f"t={3*(i+1)}s title={t!r}")
            if t and "leboncoin" in str(t):
                break
        except Exception as e:
            print(f"t={3*(i+1)}s eval err", str(e)[:60])
    time.sleep(5)  # 让懒加载/chunk 请求走完
    reqs = call("list_network_requests")
    if isinstance(reqs, dict): reqs = reqs.get("result", [reqs])
    statuses = Counter(str(r.get("status")) for r in reqs)
    print("total requests:", len(reqs))
    print("status:", dict(statuses))
    n404 = [r for r in reqs if r.get("status") == 404]
    print("404 count:", len(n404))
    for r in n404:
        print("  404:", r.get("url"))
    stats = call("vm_loop_trace", action="stop")
    removed = stats.get("removed") or []
    if removed:
        print("route_stats:", json.dumps(removed[0].get("stats"), ensure_ascii=False))
    try: call("close_browser")
    except Exception: pass
