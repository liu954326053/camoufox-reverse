#!/usr/bin/env python3
"""渲染 vm_loop 运行时供 node 契约验证。

用法：python3 tests/render_runtime.py <out.js> [patched-out.js]
在 integrations/camoufox-reverse-mcp 目录下运行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from camoufox_reverse_mcp.tools.vm_loop import _runtime_js

PATCHES = [
    {"name": "count-register",
     "pattern": r"function Du\((\w+),(\w+),(\w+)\)\{",
     "replacement": ("function Du($1,$2,$3){"
                     "(globalThis.__mcp_vm_counters=globalThis.__mcp_vm_counters"
                     "||{registered:{}}).registered[$2]="
                     "(globalThis.__mcp_vm_counters.registered[$2]|0)+1;")},
    {"name": "seq-register",
     "pattern": r"function Du\((\w+),(\w+),(\w+)\)\{",
     "replacement": ("function Du($1,$2,$3){"
                     "(globalThis.__mcp_vm_rec&&"
                     "globalThis.__mcp_vm_rec('Du',[$2]));")},
    # 第十阶段切片 A：结构签名锚点——同一锚点的改名免疫版本。
    # 标识符写 _+，关键字保持字面量；$1..$4 恢复真实名字。
    {"name": "struct-register",
     "structural": True,
     "pattern": r"function\s+(_+)\s*\(\s*(_+)\s*,\s*(_+)\s*,\s*(_+)\s*\)\s*\{",
     "replacement": ("function $1($2,$3,$4){"
                     "(globalThis.__mcp_vm_struct=globalThis.__mcp_vm_struct"
                     "||[]).push($3);")},
]


def main():
    out1 = sys.argv[1]
    Path(out1).write_text(
        _runtime_js(64, state_vars=["N", "A"], tick_times=True))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(
            _runtime_js(64, state_vars=["N", "A"], tick_times=True,
                        code_patches=PATCHES))
    print("rendered", out1, sys.argv[2] if len(sys.argv) > 2 else "")


if __name__ == "__main__":
    main()
