"""循环角色分类器：从 vm-loop artifact + 动态源码还原各循环的执行角色。

只读 raw/vm-loop/*.json，分类规则全部可解释并标注证据等级：
- 源码签名命中（位流读取器、XOR 流、取模采样、charCodeAt）→ observed；
- 周期 FSM × 迭代数 ≈ 程序字节数 且含 charCodeAt → string-decoder（observed）；
- 只有周期性没有源码签名 → periodic-fsm（inferred）；
- 无签名无周期 → unknown（gap），附状态直方图供人工判读。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --- 源码签名（取自复测轮 266b076a 真实代码形态，全部目标无关的语法模式）---

# 位流读取器：for(;z<n;)D|=f(a)<<z,z+=8 —— 解压器的标志
_BITREADER_RE = re.compile(r"for\s*\(;\s*\w+\s*<\s*\w+\s*;\s*\)\s*\w+\s*\|=\s*\w+\([^)]*\)\s*<<\s*\w+\s*,\s*\w+\s*\+=\s*8")
# 解压头：u=(P(3)|0)+1,b=P(5) —— 表数量 + 位宽
_DECOMP_HEADER_RE = re.compile(r"\w+\s*=\s*\(\s*\w+\(3\)\s*\|\s*0\s*\)\s*\+\s*1\s*,\s*\w+\s*=\s*\w+\(5\)")
# 取模采样排表：h=(h+k)%u 紧跟 Y[f](D[h]) 式写入
_PERMUTATION_RE = re.compile(r"%\s*\w+\s*,\s*\w+\[\w+\]\(\w+\[\w+\]\)")
# XOR 流加密：push(...^...)
_XOR_PUSH_RE = re.compile(r"\.push\([^()]*\^")
# 变长位读取（程序解密/位流）：n|=(X>>(...)&(1<<M)-1)<<(…) 的位掩码累积
_BITMASK_ACC_RE = re.compile(r"\|=\s*\([^;]*&\s*\(\s*1\s*<<\s*\w+\s*\)\s*-\s*1")
# 轮转加密轮函数：循环体内 >>> 与 <<24 旋转移位 + ^= 混合
_ROT24_RE = re.compile(r"<<\s*24")
_ROTATE_RE = re.compile(r">>>")
_XOR_MIX_RE = re.compile(r"\^=")
# 字符串表访问器的混淆空转循环：while(0==![]){return ...;if({})break}
_JUNK_LOOP_RE = re.compile(r"while\s*\(\s*0\s*==\s*!\[\]\s*\)")
# 字符串解码：charCodeAt 逐字符遍历
_CHARCODE_RE = re.compile(r"charCodeAt")

WINDOW = 900  # 循环头之后检视的源码窗口


@dataclass
class LoopRole:
    loop: str
    iterations: int
    role: str
    evidence: str           # observed / inferred / gap
    rationale: str
    period: int = 0
    state_histogram: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "loop": self.loop, "iterations": self.iterations,
            "role": self.role, "evidence": self.evidence,
            "rationale": self.rationale,
        }
        if self.period:
            d["period"] = self.period
        if self.state_histogram:
            d["state_histogram"] = self.state_histogram
        return d


def _shortest_period(seq: list, cap: int = 64, prologue: int = 16) -> int:
    """状态序列的最短严格周期（0 = 无周期）。

    平坦化 FSM 常有一次性 prologue（初始化分支），允许跳过前 ≤16 个状态后
    再判定周期：真实解码器循环就是 5 状态 prologue + 5 状态定周期。
    """
    n = len(seq)
    for skip in range(0, min(prologue, n // 4) + 1):
        tail = seq[skip:]
        m = len(tail)
        for plen in range(1, min(cap, m // 2) + 1):
            if all(tail[i] == tail[i % plen] for i in range(m)):
                return plen
    return 0


def _first_values(loop: dict) -> list:
    out = []
    for snap in loop.get("states") or []:
        v = snap[0] if snap else None
        out.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    return out


def _source_for(artifact: dict, loop_id: str) -> tuple[str | None, int]:
    """loop id = L<hash>_<offset>；从 dynamic_sources 找源码与循环头偏移。"""
    try:
        src_hash, offset = loop_id[1:].rsplit("_", 1)
        off = int(offset)
    except ValueError:
        return None, -1
    for s in artifact.get("dynamic_sources") or []:
        if s.get("hash") == src_hash and s.get("source"):
            return s["source"], off
    return None, off


def classify_loop(loop: dict, source: str | None, offset: int,
                  program_size: int | None = None) -> LoopRole:
    lid = loop.get("loop", "?")
    iters = int(loop.get("iterations") or 0)
    vals = _first_values(loop)
    period = _shortest_period(vals) if vals else 0
    body = source[offset:offset + WINDOW] if source and offset >= 0 else ""
    hist = {str(k): v for k, v in
            Counter(str(v) for v in vals).most_common(16)} if vals else {}

    def role(r: str, ev: str, why: str) -> LoopRole:
        return LoopRole(lid, iters, r, ev, why, period=period,
                        state_histogram=hist if r == "unknown" else {})

    if body:
        # 解压器：循环体窗口内同时出现位流读取器与解压头
        if _BITREADER_RE.search(body) or (
                _DECOMP_HEADER_RE.search(body) and ">>=" in body):
            return role("decompressor", "observed",
                        "循环窗口含位流读取器/解压头签名")
        # 解压器的宿主 FSM：位流读取器定义在循环体靠后位置（P=function...）
        widen = source[max(0, offset - 600):offset + WINDOW] if source else body
        if _BITREADER_RE.search(widen) and _DECOMP_HEADER_RE.search(widen):
            return role("decompressor", "observed",
                        "循环邻近窗口含位流读取器+解压头签名")
        if _PERMUTATION_RE.search(body):
            return role("table-permutation", "observed",
                        "循环体含取模索引采样写入（排表）签名")
        if _XOR_PUSH_RE.search(body):
            return role("cipher", "observed", "循环体含 XOR 流 push 签名")
        if _BITMASK_ACC_RE.search(body):
            return role("bit-reader", "observed",
                        "循环体含变长位掩码累积（程序位流读取/解密）签名")
        if _ROT24_RE.search(body) and _ROTATE_RE.search(body) and \
                _XOR_MIX_RE.search(body):
            return role("cipher", "observed",
                        "循环体含旋转移位轮函数签名（>>>/<<24/^= 混合）")
        if _JUNK_LOOP_RE.search(body) and "return" in body[:120]:
            return role("string-accessor", "observed",
                        "混淆空转循环包裹的立即返回（字符串表访问器形态）")

    # 周期 FSM + 程序体量相关性 → 字符串解码器
    if period and iters and program_size:
        per_cycle = iters / period
        if abs(per_cycle - program_size) / program_size < 0.05 and \
                body and _CHARCODE_RE.search(body):
            return role("string-decoder", "observed",
                        f"定长 {period} 状态周期 × {per_cycle:.0f} 周期 ≈ "
                        f"程序字节数 {program_size}，且循环体含 charCodeAt")
    if period and iters >= 100:
        return role("periodic-fsm", "inferred",
                    f"定长 {period} 状态周期（无源码签名佐证角色）")
    return role("unknown", "gap", "无源码签名、无周期规律")


def classify_artifact(artifact: dict,
                      program_size: int | None = None) -> list[LoopRole]:
    roles = []
    for loop in artifact.get("loops") or []:
        source, off = _source_for(artifact, loop.get("loop", ""))
        roles.append(classify_loop(loop, source, off, program_size))
    return roles


def write_loop_roles(session_dir: str | Path,
                     program_size: int | None = None) -> Path:
    """读 session 的 raw/vm-loop/*.json，写 derived/loop-roles.json。"""
    session = Path(session_dir)
    derived = session / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    out = derived / "loop-roles.json"

    artifacts = sorted((session / "raw" / "vm-loop").glob("*.json")) \
        if (session / "raw" / "vm-loop").is_dir() else []
    if not artifacts:
        out.write_text(json.dumps({
            "evidence": "gap",
            "detail": "raw/vm-loop 无 artifact（session 未开 --vm-loop-trace）",
            "loops": [],
        }, ensure_ascii=False, indent=2))
        return out

    # 多份 artifact（跨 realm drain）合并分类
    all_roles: list[LoopRole] = []
    for path in artifacts:
        artifact = json.loads(path.read_text())
        all_roles.extend(classify_artifact(artifact, program_size))

    summary = Counter(r.role for r in all_roles)
    out.write_text(json.dumps({
        "evidence": "observed",
        "artifacts": [p.name for p in artifacts],
        "program_size": program_size,
        "role_counts": dict(summary),
        "loops": [r.to_dict() for r in
                  sorted(all_roles, key=lambda r: -r.iterations)],
    }, ensure_ascii=False, indent=2))
    return out
