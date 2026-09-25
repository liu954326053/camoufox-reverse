"""opcode 语义假设聚合：汇总 Task 1-3 产物为 derived/opcode-hypotheses.json。

纪律：循环角色语义（Task 1 已 observed 归类的）可标 observed；
opcode handler 的具体语义一律 inferred/gap——我们只观察到注册结构和
副作用线索，没有逐条 handler 的执行语义证据，禁止升级为 observed。
"""
from __future__ import annotations

import json
from pathlib import Path

# 循环角色 → 语义断言（仅 Task 1 已 observed 的角色才有语义）
_ROLE_SEMANTICS = {
    "string-decoder": "字符串解码（逐字符 FSM）",
    "decompressor": "程序解压器（位流读取 + 类 LZ/Huffman 表构建）",
    "bit-reader": "程序位流变长读取/解密",
    "table-permutation": "handler 表置换/采样",
    "cipher": "加密原语（旋转移位轮函数 / XOR 流）",
    "string-accessor": "字符串表访问器（混淆空转循环包裹的立即返回）",
}

# 注册器 Du 中可确认副作用的代表性 opcode id（静态 observed 结构，
# 但 handler 的运行时语义只能 inferred）
_KNOWN_OPCODE_HINTS = [
    {"opcode_id": 232, "hypothesis": "注册时重置密钥流（l.W=bC(...)），"
     "疑似程序/数据解密上下文的初始化或轮换点", "evidence": "inferred"},
    {"opcode_id": 140, "hypothesis": "可追加型 handler（Y[p].concat），"
     "疑似列表/回调收集类操作", "evidence": "inferred"},
    {"opcode_id": 170, "hypothesis": "可追加型 handler（Y[p].concat），同上",
     "evidence": "inferred"},
    {"opcode_id": 166, "hypothesis": "TN(p,43,16,...) 特殊包装（与 282/14/69/"
     "283/488/499/412/366/210 同组），疑似需要额外上下文的 handler",
     "evidence": "gap"},
]


def build_hypotheses(loop_roles: dict | None,
                     dispatch_verdict: dict | None,
                     program_size: int | None = None) -> dict:
    if not loop_roles or not dispatch_verdict:
        return {
            "evidence": "gap",
            "detail": "缺少 loop-roles.json 或 dispatch-verdict.json，"
                      "先跑 Task 1/2 的分类与裁决",
        }

    loop_semantics = []
    for l in loop_roles.get("loops") or []:
        role = l.get("role", "unknown")
        known = role in _ROLE_SEMANTICS and l.get("evidence") == "observed"
        loop_semantics.append({
            "loop": l.get("loop"),
            "iterations": l.get("iterations"),
            "role": role,
            "semantics": _ROLE_SEMANTICS.get(role) if known else None,
            "evidence": "observed" if known else (
                "inferred" if role == "periodic-fsm" else "gap"),
            "rationale": l.get("rationale", ""),
        })

    return {
        "evidence": "observed",
        "program_size": program_size,
        "dispatch": {
            "form": dispatch_verdict.get("form", "unknown"),
            "evidence": dispatch_verdict.get("evidence") or [],
            "note": "table-indirect：handler 按数值 id 注册进 Y 表，"
                    "经 t(id)→entry.create() 间接调用；无集中式派发循环",
        },
        "loop_semantics": loop_semantics,
        "handler_hypotheses": list(_KNOWN_OPCODE_HINTS),
        "discipline": "循环角色语义=observed（源码签名+周期相关性）；"
                      "opcode handler 语义≤inferred（只有注册结构证据）",
    }


def write_report(session_dir: str | Path,
                 program_size: int | None = None) -> Path:
    session = Path(session_dir)
    derived = session / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    out = derived / "opcode-hypotheses.json"

    def _load(name: str):
        p = derived / name
        return json.loads(p.read_text()) if p.exists() else None

    report = build_hypotheses(_load("loop-roles.json"),
                              _load("dispatch-verdict.json"),
                              program_size)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return out
