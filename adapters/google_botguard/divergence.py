"""Task 5: 浏览器轨迹与外部 VM 复现实现的首个分歧报告。

对齐对象：浏览器侧派发循环的状态序列（vm_loop_trace 轨迹中迭代数最大的循环，
每个快照取首个元素=派发状态值）与外部实现（如 Go 复现 VM）导出的状态序列。

契约：
- 外部实现不存在 / 轨迹缺失 / 任一侧为空 → status="gap"，写明原因，不伪造对比；
- 两侧前缀一致但长度不同 → status="aligned-prefix"；
- 首个不一致处 → status="diverged"，给出索引、两侧状态值与证据等级。

只读 raw/，只写 derived/vm-divergence.json。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .trace_link import VmExecution


@dataclass
class DivergenceReport:
    status: str  # aligned | aligned-prefix | diverged | gap
    browser_states: int = 0
    external_states: int = 0
    compared: int = 0
    first_divergence: dict | None = None  # index/browser/external
    detail: str = ""
    artifacts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "status": self.status,
            "browser_states": self.browser_states,
            "external_states": self.external_states,
            "compared": self.compared,
            "first_divergence": self.first_divergence,
            "detail": self.detail or None,
            "artifacts": self.artifacts or None,
        }


def browser_state_sequence(session_dir: str | Path) -> tuple[list, str | None]:
    """浏览器侧派发状态序列：取迭代数最大循环的快照首元素。

    返回 (序列, 来源 artifact)；无轨迹时返回 ([], None)。
    第十阶段 E1 兼容：states 超阈值时被流式拆到旁车 NDJSON
    （loop.states_file 指针，session 相对路径），跟着指针读。
    """
    execution = VmExecution.load(session_dir)
    if not execution.found or not execution.dispatch:
        return [], None
    artifact = execution.artifacts[0] if execution.artifacts else None
    loop_id = execution.dispatch.get("loop")
    for path in execution.artifacts:
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for loop in data.get("loops", []):
            if loop.get("loop") != loop_id:
                continue
            raw_states = loop.get("states")
            if not raw_states and loop.get("states_file"):
                sidecar = Path(session_dir) / loop["states_file"]
                raw_states = []
                try:
                    for line in sidecar.read_text().splitlines():
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        if row.get("loop") == loop_id:
                            raw_states.append((row.get("i", 0),
                                               row.get("state")))
                    raw_states = [s for _, s in sorted(raw_states)]
                except (OSError, json.JSONDecodeError):
                    raw_states = []
            states = []
            for snapshot in raw_states or []:
                if isinstance(snapshot, list) and snapshot:
                    states.append(snapshot[0])
                else:
                    states.append(snapshot)
            return states, path
    return [], artifact


def load_external_states(path: str | Path) -> list:
    """外部实现的状态序列文件：JSON 数组，或 {"states": [...]}。"""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("states", [])
    if not isinstance(data, list):
        raise ValueError("external trace must be a JSON array or {'states': [...]}")
    return data


def compare_sequences(browser: list, external: list) -> DivergenceReport:
    """逐位对齐两个状态序列，返回首个分歧（或对齐结论）。"""
    compared = min(len(browser), len(external))
    for index in range(compared):
        if browser[index] != external[index]:
            return DivergenceReport(
                status="diverged",
                browser_states=len(browser),
                external_states=len(external),
                compared=index + 1,
                first_divergence={
                    "index": index,
                    "browser": browser[index],
                    "external": external[index],
                    "evidence": "observed",
                },
            )
    if len(browser) == len(external):
        status = "aligned"
    else:
        status = "aligned-prefix"
    return DivergenceReport(
        status=status,
        browser_states=len(browser),
        external_states=len(external),
        compared=compared,
    )


def build_divergence_report(session_dir: str | Path,
                            external_trace: str | Path | None = None
                            ) -> DivergenceReport:
    """生成分歧报告并写入 derived/vm-divergence.json。"""
    session = Path(session_dir)
    browser, browser_artifact = browser_state_sequence(session)
    if not browser:
        report = DivergenceReport(
            status="gap",
            detail="browser dispatch trace missing; "
                   "run the flow with vm_loop_trace installed",
        )
    elif external_trace is None:
        report = DivergenceReport(
            status="gap",
            browser_states=len(browser),
            detail="no external VM re-implementation trace provided; "
                   "comparison not fabricated",
            artifacts={"browser_trace": browser_artifact} if browser_artifact else {},
        )
    else:
        external_path = Path(external_trace)
        if not external_path.is_file():
            report = DivergenceReport(
                status="gap",
                browser_states=len(browser),
                detail=f"external trace not found: {external_path}",
                artifacts={"browser_trace": browser_artifact} if browser_artifact else {},
            )
        else:
            external = load_external_states(external_path)
            if not external:
                report = DivergenceReport(
                    status="gap",
                    browser_states=len(browser),
                    detail="external trace contains no states",
                    artifacts={"browser_trace": browser_artifact,
                               "external_trace": str(external_path)},
                )
            else:
                report = compare_sequences(browser, external)
                report.artifacts = {
                    "browser_trace": browser_artifact,
                    "external_trace": str(external_path),
                }
    derived = session / "derived"
    derived.mkdir(exist_ok=True)
    out = derived / "vm-divergence.json"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)
    return report
