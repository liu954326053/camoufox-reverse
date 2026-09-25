"""值生成链关联：把 VM 程序、proof、请求和发起栈写入 derived/vm-evidence.json。

只读 raw/，只写 derived/。每个节点标注证据等级；缺失环节输出 gap。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .program import BotGuardProgram
from .proof import ProofToken
from .trace_link import EnvInputs, VmExecution


def build_vm_evidence(session_dir: str | Path) -> dict:
    session = Path(session_dir)
    program = BotGuardProgram.extract(session)
    proof = ProofToken.locate(session)
    execution = VmExecution.load(session)
    env_inputs = EnvInputs.load(session)

    chain = []
    if proof.found:
        chain.append({
            "node": "request_field",
            "description": "batchexecute f.req 中的 VM proof 值",
            "evidence": "observed",
            "rpcid": proof.rpcid,
            "path_in_f_req": proof.path,
            "artifact": proof.request_artifact,
        })
        chain.append({
            "node": "initiator_stack",
            "description": "proof 请求的发起调用栈",
            "evidence": "observed" if proof.stack else "gap",
            "stack_head": (proof.stack or "").splitlines()[-3:],
        })
    else:
        chain.append({
            "node": "request_field",
            "description": proof.detail,
            "evidence": proof.evidence,
            "candidates": [c[:12] + "..." for c in proof.candidates],
        })
    if program.found:
        chain.append({
            "node": "vm_program",
            "description": "BotGuard VM 程序字节（页面内嵌 base64）",
            "evidence": "observed",
            "sha256": program.sha256,
            "size": len(program.bytes),
            "artifact": program.source_artifact,
        })
    else:
        chain.append({
            "node": "vm_program",
            "description": program.detail,
            "evidence": "gap",
            "candidates": program.candidates,
        })
    chain.append({
        "node": "vm_execution",
        "description": "VM 派发循环轨迹（vm_loop_trace drainAll 聚合）",
        **({
            "evidence": "observed",
            "realms": execution.realms,
            "loops": execution.loops,
            "total_iterations": execution.total_iterations,
            "dispatch_loop": execution.dispatch,
            "artifacts": execution.artifacts,
        } if execution.found else {
            "evidence": "gap",
            "detail": execution.detail,
        }),
    })
    chain.append({
        "node": "environment_inputs",
        "description": "原生 PropertyTracer 记录的环境属性读取（navigator/window 等）",
        **({
            "evidence": "observed",
            "event_count": env_inputs.event_count,
            "top_properties": env_inputs.top_properties,
            "trace_files": env_inputs.trace_files,
        } if env_inputs.found else {
            "evidence": "gap",
            "detail": env_inputs.detail,
        }),
    })

    report = {
        "schema": 1,
        "program": {
            "evidence": program.evidence,
            "sha256": program.sha256,
            "size": len(program.bytes) if program.found else None,
            "source_artifact": program.source_artifact,
            "detail": program.detail or None,
        },
        "proof": {
            "evidence": proof.evidence,
            "value_prefix": proof.value[:12] if proof.value else None,
            "length": len(proof.value) if proof.value else None,
            "rpcid": proof.rpcid,
            "path_in_f_req": proof.path or None,
            "request_artifact": proof.request_artifact,
            "candidates": [c[:12] + "..." for c in proof.candidates] or None,
            "detail": proof.detail or None,
        },
        "vm_execution": {
            "evidence": execution.evidence,
            "realms": execution.realms or None,
            "loops": execution.loops or None,
            "total_iterations": execution.total_iterations or None,
            "dispatch_loop": execution.dispatch,
            "artifacts": execution.artifacts or None,
            "detail": execution.detail or None,
        },
        "environment_inputs": {
            "evidence": env_inputs.evidence,
            "event_count": env_inputs.event_count or None,
            "top_properties": env_inputs.top_properties or None,
            "trace_files": env_inputs.trace_files or None,
            "detail": env_inputs.detail or None,
        },
        "chain": chain,
    }
    derived = session / "derived"
    derived.mkdir(exist_ok=True)
    out = derived / "vm-evidence.json"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report
