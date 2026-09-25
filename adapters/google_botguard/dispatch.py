"""派发形态裁决：BotGuard VM 是集中式派发循环还是表间接调用。

只读 raw/vm-loop artifact 里的动态源码，输出 derived/dispatch-verdict.json。
证据全部来自静态源码签名（observed），不足时如实 unknown。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 注册器：l.Y[p]=... 形式把 handler 写入表（Du 形态）
_REGISTRAR_RE = re.compile(r"\w+\.\w{0,2}Y\[\w+\]\s*=")
# 表项经 .create() 调用（t 派发器形态：c=p.Y[l] 之后若干语句内 .create()）
_CREATE_DISPATCH_RE = re.compile(r"=\s*\w+\.\w{0,2}Y\[\w+\][\s\S]{0,120}?\.create\(\)")
# 作用域栈：TY.push(Y.slice()) —— VM 调用帧的压栈
_SCOPE_STACK_RE = re.compile(r"\.TY\.push\(\s*\w+\.\w{0,2}Y\.slice\(\)\s*\)")

_GIANT_SWITCH_MIN = 40  # 超过 40 case 的 switch 才算集中式派发候选


def _max_switch_cases(src: str) -> int:
    best = 0
    for m in re.finditer(r"switch\s*\(", src):
        seg = src[m.start():m.start() + 8000]
        # 数到 switch 平衡结束太贵，用窗口近似（对比基线：DOM 工具 switch ~31 case）
        best = max(best, seg.count("case "))
    return best


def verdict_for_source(src: str) -> dict:
    evidence = []
    if _REGISTRAR_RE.search(src):
        evidence.append({
            "kind": "handler-table-registrar", "level": "observed",
            "detail": "源码含 l.Y[p]=handler 注册器（Du 形态），"
                      "handler 按数值 id 入表"})
    if _CREATE_DISPATCH_RE.search(src):
        evidence.append({
            "kind": "create-dispatch", "level": "observed",
            "detail": "派发经 c=p.Y[id] 取表项后 .create() 间接调用，"
                      "调用点分散在各函数内"})
    if _SCOPE_STACK_RE.search(src):
        evidence.append({
            "kind": "scope-stack", "level": "observed",
            "detail": "TY.push(Y.slice()) 作用域栈：VM 调用帧压/弹 handler 表"})
    max_cases = _max_switch_cases(src)
    if max_cases >= _GIANT_SWITCH_MIN:
        evidence.append({
            "kind": "giant-switch", "level": "observed",
            "detail": f"存在 {max_cases} case 的巨型 switch（集中式派发候选）"})

    table_score = sum(1 for e in evidence
                      if e["kind"] in ("handler-table-registrar",
                                       "create-dispatch", "scope-stack"))
    if max_cases >= _GIANT_SWITCH_MIN and table_score == 0:
        form = "centralized"
    elif table_score >= 2:
        form = "table-indirect"
    else:
        form = "unknown"
    return {"form": form, "evidence": evidence,
            "max_switch_cases": max_cases}


def write_verdict(session_dir: str | Path) -> Path:
    session = Path(session_dir)
    derived = session / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    out = derived / "dispatch-verdict.json"

    artifacts = sorted((session / "raw" / "vm-loop").glob("*.json")) \
        if (session / "raw" / "vm-loop").is_dir() else []
    sources = []
    for path in artifacts:
        artifact = json.loads(path.read_text())
        for s in artifact.get("dynamic_sources") or []:
            if s.get("source"):
                sources.append((s.get("hash"), s["source"]))

    if not sources:
        out.write_text(json.dumps({
            "form": "unknown", "evidence": "gap",
            "detail": "raw/vm-loop 无带源码的 artifact",
        }, ensure_ascii=False, indent=2))
        return out

    verdicts = []
    for h, src in sources:
        v = verdict_for_source(src)
        v["source_hash"] = h
        verdicts.append(v)

    # 任一源码给出 table-indirect 即为整体结论（VM 脚本只有一个）
    best = next((v for v in verdicts if v["form"] == "table-indirect"), None) or \
           next((v for v in verdicts if v["form"] == "centralized"), None) or \
           {"form": "unknown", "evidence": [], "max_switch_cases": 0}
    best["per_source"] = [
        {"hash": v["source_hash"], "form": v["form"],
         "max_switch_cases": v["max_switch_cases"]} for v in verdicts]
    out.write_text(json.dumps(best, ensure_ascii=False, indent=2))
    return out
