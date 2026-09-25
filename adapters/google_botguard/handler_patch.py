"""BotGuard handler 计数补丁（第四阶段 Task 5，目标特定）。

经通用代码补丁通道（vm_loop_trace 的 code_patches）包装两个关键函数：

- 注册器 ``Du(l,p,c)``（handler-table-registrar，第三阶段 observed）：
  函数头插入计数，按 opcode id（参数 p）累计注册次数；
- 派发器 ``t(l,p)``（create-dispatch，第三阶段 observed）：
  函数头插入计数，按 opcode id（参数 l）累计 .create() 派发次数。

计数写入 ``globalThis.__mcp_vm_counters.{registered,dispatched}``，
随 vm_loop drain 跨 realm 合并回收；``write_handler_counts`` 把 session
raw/vm-loop artifact 里的 counters 落成 derived/handler-counts.json。

混淆随机化意味着函数名每个 build 都变：补丁构造参数化，
默认值为 2026-09-25 build 的实测名（Du/t），换 build 只需改参数。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

COUNTER_GLOBAL = "__mcp_vm_counters"

_INIT = ("(globalThis.%s=globalThis.%s||{registered:{},dispatched:{}})"
         % (COUNTER_GLOBAL, COUNTER_GLOBAL))


def _fn_head_pattern(name: str, arity: int) -> str:
    """匹配 function 声明或赋值形态:function f(a,b){ / f=function(a,b){"""
    params = ",".join(r"(\w+)" for _ in range(arity))
    return r"(?:function\s+%s|%s\s*=\s*function)\(%s\)\{" % (
        re.escape(name), re.escape(name), params)


def handler_count_patches(register_fn: str = "Du",
                          dispatch_fn: str = "t",
                          dispatch_arity: int = 3) -> list[dict]:
    """构造 handler 注册/派发计数补丁（喂给 vm_loop_trace 的 code_patches）。

    注册器 Du(l,p,c)：第二个参数（$2）是 opcode 数值 id；
    派发器 t(l,p,c)：第一个参数（$1）是 opcode id（2026-09-25 build 实测
    为三元 ``t=function(l,p,c){c=p.Y[l];...c.create()...}``，故默认
    dispatch_arity=3）。可选前缀组非捕获，两形态捕获组位置一致。
    """
    return [
        {
            "name": "count-register",
            "pattern": _fn_head_pattern(register_fn, 3),
            "replacement": ("$&" + _INIT +
                            ".registered[$2]=(" +
                            "globalThis.%s.registered[$2]|0)+1;" % COUNTER_GLOBAL),
        },
        {
            "name": "count-dispatch",
            "pattern": _fn_head_pattern(dispatch_fn, dispatch_arity),
            "replacement": ("$&" + _INIT +
                            ".dispatched[$1]=(" +
                            "globalThis.%s.dispatched[$1]|0)+1;" % COUNTER_GLOBAL),
        },
    ]


def write_handler_counts(session_dir: str | Path) -> Path:
    """聚合 session raw/vm-loop artifact 的 counters，落 derived/handler-counts.json。

    counters 已由 vm_loop drain 跨 realm 合并；多个 artifact（多次 drain）
    之间按数值叶子求和再合并一次。
    """
    session = Path(session_dir)
    derived = session / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    out = derived / "handler-counts.json"

    def _merge(dst: dict, src: dict) -> None:
        for key, val in src.items():
            if isinstance(val, dict) and isinstance(dst.get(key), dict):
                _merge(dst[key], val)
            elif (isinstance(val, (int, float))
                  and isinstance(dst.get(key), (int, float))):
                dst[key] += val
            else:
                dst[key] = val

    merged: dict = {}
    found = 0
    vm_loop_dir = session / "raw" / "vm-loop"
    artifacts = sorted(vm_loop_dir.glob("*.json")) if vm_loop_dir.is_dir() else []
    for path in artifacts:
        artifact = json.loads(path.read_text())
        counters = artifact.get("counters")
        if isinstance(counters, dict):
            found += 1
            _merge(merged, counters)

    payload = {
        "registered": merged.get("registered", {}),
        "dispatched": merged.get("dispatched", {}),
        "artifacts_with_counters": found,
        "artifacts_scanned": len(artifacts),
        "level": "observed" if found else "gap",
        "note": "计数来自代码补丁通道对注册器/派发器函数头的包装；"
                "未匹配（混淆改名）时为空，需按 build 实测名调整参数",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out
