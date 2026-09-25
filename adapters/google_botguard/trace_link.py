"""Task 3: VM 执行轨迹与环境输入的回溯读取（只读 raw/ 与 trace/）。

- VmExecution: 汇总 raw/vm-loop/*.json（vm_loop_trace 工具的 drainAll 产物），
  给出 realm 分布、迭代总量与派发循环（迭代数最大的循环）的状态序列节选。
- EnvInputs: 汇总 trace/traces/*.jsonl 的原生 PropertyTracer 事件
  （{"o":对象,"p":属性,...}），给出事件数与被读属性 Top N。

找不到对应产物时返回 evidence="gap"，不伪造。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_DISPATCH_STATE_EXCERPT = 32
_TOP_PROPERTIES = 25


@dataclass
class VmExecution:
    found: bool
    evidence: str  # observed | gap
    artifacts: list[str] = field(default_factory=list)
    realms: list[str] = field(default_factory=list)
    loops: int = 0
    total_iterations: int = 0
    dispatch: dict | None = None  # 迭代数最大的循环：id/realm/iterations/状态节选
    detail: str = ""

    @staticmethod
    def load(session_dir: str | Path) -> "VmExecution":
        vm_dir = Path(session_dir) / "raw" / "vm-loop"
        if not vm_dir.exists():
            return VmExecution(found=False, evidence="gap",
                               detail="no raw/vm-loop artifacts; "
                                      "run with vm_loop_trace installed")
        artifacts = sorted(vm_dir.glob("*.json"))
        if not artifacts:
            return VmExecution(found=False, evidence="gap",
                               detail="raw/vm-loop exists but empty")
        loops: list[dict] = []
        realms: set[str] = set()
        for artifact in artifacts:
            try:
                data = json.loads(artifact.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            for loop in data.get("loops", []):
                loops.append(loop)
                if loop.get("realm"):
                    realms.add(loop["realm"])
        if not loops:
            return VmExecution(found=False, evidence="gap",
                               artifacts=[str(a) for a in artifacts],
                               detail="vm-loop artifacts contain no loops")
        dispatch_loop = max(loops, key=lambda l: l.get("iterations", 0))
        return VmExecution(
            found=True, evidence="observed",
            artifacts=[str(a) for a in artifacts],
            realms=sorted(realms),
            loops=len(loops),
            total_iterations=sum(l.get("iterations", 0) for l in loops),
            dispatch={
                "loop": dispatch_loop.get("loop"),
                "realm": dispatch_loop.get("realm"),
                "iterations": dispatch_loop.get("iterations", 0),
                "states_recorded": dispatch_loop.get("states_recorded", 0),
                "truncated": dispatch_loop.get("truncated", False),
                "states_excerpt": dispatch_loop.get("states", [])[
                    :_DISPATCH_STATE_EXCERPT],
            },
        )


@dataclass
class EnvInputs:
    found: bool
    evidence: str  # observed | gap
    event_count: int = 0
    top_properties: list[dict] = field(default_factory=list)
    trace_files: list[str] = field(default_factory=list)
    detail: str = ""

    @staticmethod
    def load(session_dir: str | Path) -> "EnvInputs":
        traces_dir = Path(session_dir) / "trace" / "traces"
        if not traces_dir.exists():
            return EnvInputs(found=False, evidence="gap",
                             detail="no trace/traces directory; "
                                    "launch with enable_trace")
        files = sorted(traces_dir.glob("*.jsonl"))
        counter: Counter[str] = Counter()
        used_files: list[str] = []
        event_count = 0
        for trace_file in files:
            file_events = 0
            try:
                handle = trace_file.open()
            except OSError:
                continue
            with handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    obj, prop = event.get("o"), event.get("p")
                    if not obj or not prop:
                        continue
                    counter[f"{obj}.{prop}"] += 1
                    file_events += 1
            if file_events:
                used_files.append(str(trace_file))
                event_count += file_events
        if not event_count:
            return EnvInputs(found=False, evidence="gap",
                             detail="trace files contain no property events")
        return EnvInputs(
            found=True, evidence="observed", event_count=event_count,
            top_properties=[{"property": name, "reads": count}
                            for name, count in counter.most_common(_TOP_PROPERTIES)],
            trace_files=used_files,
        )
