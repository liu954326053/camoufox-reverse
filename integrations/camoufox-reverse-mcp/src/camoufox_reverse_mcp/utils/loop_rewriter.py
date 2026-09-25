"""loop_rewriter.py - 通用解释器循环插桩（Task 2）。

在任何 while(true) 派发循环的循环体头部注入 tick 调用，记录状态变量快照。
不认识任何具体 VM 的语义；状态变量名由调用方按目标适配器给出。

注入形式（循环体头部）：
    if (window.__mcp_vm_loop_tick) window.__mcp_vm_loop_tick("L<offset>", {...});
"""
from __future__ import annotations

import json
import re
from typing import Any

from .ast_rewriter import _walk


def inject_loop_ticks(
    src: str,
    state_vars: list[str] | None = None,
    max_loops: int = 50,
) -> tuple[str | None, dict]:
    """Rewrite JS source, inserting a tick call at every while(true) loop head.

    Returns (rewritten_source, stats) or (None, stats) on parse failure.
    """
    import esprima

    stats: dict[str, Any] = {"parsed": False, "loops": 0}
    try:
        tree = esprima.parseScript(src, options={"range": True, "tolerant": True})
        stats["parsed"] = True
    except Exception as e:
        stats["error"] = f"parse_failed: {type(e).__name__}: {e}"
        return None, stats

    insertions: list[tuple[int, str]] = []
    loops_found = 0

    def on_node(node, _parent):
        nonlocal loops_found
        if loops_found >= max_loops:
            return
        body = getattr(node, "body", None)
        node_range = getattr(node, "range", None)
        body_range = getattr(body, "range", None) if body else None
        if not body or not body_range or not node_range:
            return
        if node.type == "WhileStatement":
            test = getattr(node, "test", None)
            if not test or test.type != "Literal":
                return
            value = getattr(test, "value", None)
            # while(true) 与 JSVMP 常见的 while(1) 都算无限派发循环；
            # 严格区分布尔与数字（True==1 但 1 是 Literal 数字形态）。
            if not (value is True or (isinstance(value, (int, float))
                                      and not isinstance(value, bool)
                                      and value == 1)):
                return
        elif node.type == "ForStatement":
            # for(;;) 无条件表达式也是无限循环（FSM 派发常见形态）
            if getattr(node, "test", None) is not None:
                return
        else:
            return
        loops_found += 1
        loop_id = f"L{node_range[0]}"
        if state_vars:
            snapshot = "{" + ",".join(
                f"{json.dumps(v)}:(typeof {v}==='undefined'?null:{v})"
                for v in state_vars
            ) + "}"
        else:
            snapshot = "null"
        tick = (f"if(window.__mcp_vm_loop_tick)"
                f"window.__mcp_vm_loop_tick({json.dumps(loop_id)},{snapshot});")
        if body.type == "BlockStatement":
            insertions.append((body_range[0] + 1, tick))
        else:
            # 非块循环体（如 while(true)try{...}）：包成块再注入
            insertions.append((body_range[1], "}"))
            insertions.append((body_range[0], "{" + tick))

    _walk(tree, None, on_node)

    out = src
    for position, tick in sorted(insertions, key=lambda item: -item[0]):
        out = out[:position] + tick + out[position:]
    stats["loops"] = loops_found
    return out, stats


_SCRIPT_RE = re.compile(r"(<script\b[^>]*>)(.*?)(</script>)", re.S | re.I)


def rewrite_inline_scripts(
    html: str,
    state_vars: list[str] | None = None,
    max_loops: int = 50,
) -> tuple[str, dict]:
    """Rewrite while(true) loops inside inline <script> blocks of an HTML page.

    External scripts (with src=) and scripts without dispatch loops pass
    through byte-identical. Tag attributes (nonce etc.) are preserved.
    """
    stats: dict[str, Any] = {"scripts_seen": 0, "scripts_rewritten": 0,
                             "loops": 0, "parse_failures": 0}

    def replace(match: re.Match) -> str:
        open_tag, body, close_tag = match.group(1), match.group(2), match.group(3)
        if "src=" in open_tag.lower() or not body.strip():
            return match.group(0)
        stats["scripts_seen"] += 1
        rewritten, rstats = inject_loop_ticks(body, state_vars=state_vars,
                                              max_loops=max_loops)
        if rewritten is None:
            stats["parse_failures"] += 1
            return match.group(0)
        if rstats["loops"] == 0:
            return match.group(0)
        stats["scripts_rewritten"] += 1
        stats["loops"] += rstats["loops"]
        return open_tag + rewritten + close_tag

    return _SCRIPT_RE.sub(replace, html), stats
