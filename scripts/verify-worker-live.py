#!/usr/bin/env python3.12
"""Worker realm 全量插桩的真实浏览器端到端验证（第四阶段 Task 1 验收）。

用项目运行时 AsyncReverseBrowser 起 camoufox（reverse.9，main_world_eval），
加载合成 fixture 页（Worker FSM + 嵌套 Worker），先装 hook 再触发 Worker
创建，drainAllAsync 聚合校验：
  - worker[0] 出现，主 Worker 循环 3 次迭代、FSM 状态序列 [1,2,3]；
  - worker[0] 内嵌套 Worker（worker[0]/worker[0]）循环 2 次迭代。

用法: /usr/local/bin/python3.12 scripts/verify-worker-live.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pythonlib"))
sys.path.insert(0, str(ROOT / "integrations" / "camoufox-reverse-mcp" / "src"))

from camoufox.reverse_runtime import AsyncReverseBrowser  # noqa: E402
from camoufox_reverse_mcp.tools.vm_loop import _runtime_js  # noqa: E402

FIXTURE = (ROOT / "integrations" / "camoufox-reverse-mcp" / "tests" /
           "fixtures" / "worker_fsm.html")
PROJECT = ROOT / "artifacts" / "analysis" / "phase4" / "worker-live"


async def run() -> list[str]:
    failures = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    async with AsyncReverseBrowser(PROJECT, headless=True,
                                   main_world_eval=True) as rb:
        page = rb.page
        await page.goto(FIXTURE.as_uri())
        # hook 必须先于 Worker 创建装入主世界（mw: 前缀 = main world）
        await page.evaluate("mw:" + _runtime_js(1000))
        await page.evaluate("mw:() => window.__start()")
        await page.wait_for_timeout(600)  # Worker 执行 + 嵌套 Worker 完成
        raw = await page.evaluate(
            "mw:() => window.__mcp_vm_loop_state.drainAllAsyncText(1500)")

    realms = json.loads(raw)
    print("realms:", [r["realm"] for r in realms])

    w0 = next((r for r in realms if r["realm"] == "worker[0]"), None)
    check("worker[0] 聚合成功", bool(w0) and "error" not in w0)

    # Worker 桥回传的是该 Worker 的 drainAllAsync 数组：
    # [{realm:'top',...}, {realm:'worker[0]',...嵌套...}]
    w0_own = w0_nested = None
    if w0 and isinstance(w0.get("data"), list):
        w0_own = next((e for e in w0["data"] if e["realm"] == "top"), None)
        w0_nested = next((e for e in w0["data"]
                          if e["realm"] == "worker[0]"), None)
    own_loops = (w0_own or {}).get("data", {}).get("loops", [])
    check("主 Worker 循环 3 次迭代",
          len(own_loops) == 1 and own_loops[0]["iterations"] == 3)
    check("主 Worker FSM 状态序列正确",
          bool(own_loops) and
          [s[0] for s in own_loops[0]["states"]] == [1, 2, 3])

    nested_loops = []
    if w0_nested and isinstance(w0_nested.get("data"), list):
        nested_own = next((e for e in w0_nested["data"]
                           if e["realm"] == "top"), None)
        nested_loops = (nested_own or {}).get("data", {}).get("loops", [])
    check("嵌套 Worker（worker[0] 内）聚合成功", bool(w0_nested))
    check("嵌套 Worker 循环 3 次迭代、状态 [1,5,9]",
          len(nested_loops) == 1 and nested_loops[0]["iterations"] == 3 and
          [s[0] for s in nested_loops[0]["states"]] == [1, 5, 9])

    return failures


def main() -> int:
    failures = asyncio.run(run())
    print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
