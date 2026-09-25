"""Fixture-driven tests for the BotGuard VM adapter (offline, no browser)."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from pathlib import Path

import pytest

from adapters.google_botguard import BotGuardProgram, ProofToken, build_vm_evidence

PROGRAM_BYTES = b"\x00fake-vm-program-bytes\x01\x02\xff" * 64  # 1536B, 超过最小程序体量
PROGRAM_BLOB = "prefix-data//" + base64.b64encode(PROGRAM_BYTES).decode()
PROOF_VALUE = "!" + ("Aa9_" * 200)  # 801 chars, '!'-prefixed


def make_session(tmp_path: Path) -> Path:
    session = tmp_path / "runs" / "session1"
    (session / "raw" / "network" / "doc1").mkdir(parents=True)
    (session / "raw" / "mcp-network" / "calls" / "art1").mkdir(parents=True)
    (session / "derived").mkdir(parents=True)
    (session / "manifest.json").write_text(json.dumps({"session_id": "session1"}))
    html = (
        "<html><script>AF_initDataCallback({key: 'ds:5', data:[[\""
        + PROGRAM_BLOB
        + "\",\"botguard\",null,\"[null,null]\"]], sideChannel: {}});</script></html>"
    )
    (session / "raw" / "network" / "doc1" / "response.body").write_text(html)
    (session / "raw" / "network" / "doc1" / "request.json").write_text(json.dumps({
        "method": "GET", "url": "https://accounts.google.com/v3/signin/identifier"}))
    inner = [None, "someone@example.com", None, [["x", PROGRAM_BLOB[:10], ["!short"],
             [None, PROOF_VALUE]]]]
    f_req = json.dumps([[["MI613e", json.dumps(inner), None, "generic"]]])
    body = urllib.parse.urlencode({"f.req": f_req, "at": "token"})
    (session / "raw" / "mcp-network" / "calls" / "art1" / "call1.json").write_text(json.dumps({
        "type": "xhr", "method": "POST",
        "url": "https://accounts.google.com/v3/signin/_/AccountsSignInUi/data/batchexecute?rpcids=MI613e",
        "call_id": "doc:1", "body": body, "body_ready": True,
        "stack": "_.k.send@https://www.gstatic.com/_/mss/boq-identity/app.js:1:1"}))
    return session


def test_program_extracts_bytes_and_hash(tmp_path):
    session = make_session(tmp_path)
    program = BotGuardProgram.extract(session)
    assert program.found is True
    assert program.bytes == PROGRAM_BYTES
    assert program.sha256 == hashlib.sha256(PROGRAM_BYTES).hexdigest()
    assert "response.body" in program.source_artifact


def test_program_missing_returns_gap(tmp_path):
    session = tmp_path / "runs" / "empty"
    session.mkdir(parents=True)
    program = BotGuardProgram.extract(session)
    assert program.found is False
    assert program.evidence == "gap"


def test_proof_located_with_rpcid_and_path(tmp_path):
    session = make_session(tmp_path)
    proof = ProofToken.locate(session)
    assert proof.found is True
    assert proof.value == PROOF_VALUE
    assert proof.rpcid == "MI613e"
    assert proof.evidence == "observed"
    assert "[3]" in proof.path  # nested position inside f.req


def test_proof_multiple_candidates_marked_ambiguous(tmp_path):
    session = make_session(tmp_path)
    calls = session / "raw" / "mcp-network" / "calls" / "art1"
    record = json.loads((calls / "call1.json").read_text())
    other = "!" + ("Bb8_" * 200)
    inner = [[None, [None, other]], [None, PROOF_VALUE]]
    record["body"] = urllib.parse.urlencode(
        {"f.req": json.dumps([[["MI613e", json.dumps(inner), None, "generic"]]])})
    (calls / "call2.json").write_text(json.dumps(record))
    proof = ProofToken.locate(session)
    assert proof.found is False
    assert proof.evidence == "ambiguous"
    assert sorted(proof.candidates) == sorted([PROOF_VALUE, other])


def test_chain_writes_derived_with_levels(tmp_path):
    session = make_session(tmp_path)
    report = build_vm_evidence(session)
    out = session / "derived" / "vm-evidence.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["program"]["sha256"] == hashlib.sha256(PROGRAM_BYTES).hexdigest()
    assert data["proof"]["value_prefix"] == PROOF_VALUE[:12]
    assert data["proof"]["length"] == len(PROOF_VALUE)
    assert data["proof"]["rpcid"] == "MI613e"
    assert all(node["evidence"] in ("observed", "call-linked", "inferred", "gap")
               for node in data["chain"])
    assert report["proof"]["evidence"] == "observed"
    # raw 只读：fixture raw 文件未被修改
    assert "AF_initDataCallback" in (session / "raw" / "network" / "doc1" / "response.body").read_text()


def test_chain_on_empty_session_returns_gap(tmp_path):
    session = tmp_path / "runs" / "empty"
    session.mkdir(parents=True)
    report = build_vm_evidence(session)
    assert report["program"]["evidence"] == "gap"
    assert report["proof"]["evidence"] == "gap"


# ============= Task 3: VM 执行轨迹与环境输入回溯 =============

from adapters.google_botguard import EnvInputs, VmExecution


def add_trace_fixtures(session: Path) -> None:
    """模拟 vm_loop_trace drainAll 产物 + 原生 PropertyTracer 事件。"""
    vm_dir = session / "raw" / "vm-loop"
    vm_dir.mkdir(parents=True)
    # 与 vm_loop_trace drainAll 落盘一致：扁平 loops（各带 realm 字段）
    (vm_dir / "trace1.json").write_text(json.dumps({
        "ts": 1,
        "realms": [{"realm": "top"}, {"realm": "frame[0]"}],
        "loops": [
            {"loop": "Laaa_10", "realm": "top", "iterations": 30,
             "states_recorded": 30, "truncated": False,
             "states": [[10, None], [96, None]]},
            {"loop": "Lbbb_99", "realm": "frame[0]", "iterations": 148068,
             "states_recorded": 10000, "truncated": True,
             "states": [[33], [14], [4], [70]]},
        ],
        "dynamic_sources": [],
    }))
    traces = session / "trace" / "traces"
    traces.mkdir(parents=True)
    (traces / "1_0.jsonl").write_text(
        '{"o":"navigator","p":"platform","v":"","t":864}\n'
        '{"o":"navigator","p":"platform","v":"","t":900}\n'
        '{"o":"window","p":"devicePixelRatio","v":"","t":1051}\n'
        '{"bad json\n')


def test_vm_execution_observed_with_dispatch_excerpt(tmp_path):
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    execution = VmExecution.load(session)
    assert execution.found and execution.evidence == "observed"
    assert execution.loops == 2
    assert execution.total_iterations == 148098
    assert execution.realms == ["frame[0]", "top"]
    assert execution.dispatch["loop"] == "Lbbb_99"
    assert execution.dispatch["realm"] == "frame[0]"
    assert execution.dispatch["truncated"] is True
    assert execution.dispatch["states_excerpt"] == [[33], [14], [4], [70]]


def test_env_inputs_observed_with_top_properties(tmp_path):
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    env = EnvInputs.load(session)
    assert env.found and env.evidence == "observed"
    assert env.event_count == 3
    assert env.top_properties[0] == {"property": "navigator.platform", "reads": 2}
    assert {"property": "window.devicePixelRatio", "reads": 1} in env.top_properties


def test_chain_links_execution_and_env(tmp_path):
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    report = build_vm_evidence(session)
    assert report["vm_execution"]["evidence"] == "observed"
    assert report["vm_execution"]["total_iterations"] == 148098
    assert report["environment_inputs"]["evidence"] == "observed"
    nodes = {n["node"]: n for n in report["chain"]}
    assert nodes["vm_execution"]["evidence"] == "observed"
    assert nodes["vm_execution"]["dispatch_loop"]["iterations"] == 148068
    assert nodes["environment_inputs"]["event_count"] == 3


def test_execution_and_env_gap_on_bare_session(tmp_path):
    session = make_session(tmp_path)  # 无 vm-loop / trace 产物
    report = build_vm_evidence(session)
    assert report["vm_execution"]["evidence"] == "gap"
    assert report["environment_inputs"]["evidence"] == "gap"
    nodes = {n["node"]: n for n in report["chain"]}
    assert nodes["vm_execution"]["evidence"] == "gap"
    assert "detail" in nodes["environment_inputs"]


# ============= Task 5: 分歧报告 =============

from adapters.google_botguard import build_divergence_report
from adapters.google_botguard.divergence import (
    browser_state_sequence,
    compare_sequences,
    load_external_states,
)


def test_browser_sequence_from_dispatch_loop(tmp_path):
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    states, artifact = browser_state_sequence(session)
    assert states == [33, 14, 4, 70]  # 迭代数最大循环的快照首元素
    assert artifact and "vm-loop" in artifact


def test_compare_aligned_and_prefix():
    aligned = compare_sequences([1, 2, 3], [1, 2, 3])
    assert aligned.status == "aligned" and aligned.compared == 3
    prefix = compare_sequences([1, 2, 3, 4], [1, 2, 3])
    assert prefix.status == "aligned-prefix" and prefix.compared == 3


def test_compare_first_divergence():
    report = compare_sequences([33, 14, 4, 70], [33, 14, 9, 70])
    assert report.status == "diverged"
    assert report.compared == 3  # 含分歧位
    assert report.first_divergence == {
        "index": 2, "browser": 4, "external": 9, "evidence": "observed",
    }


def test_divergence_report_gap_without_external(tmp_path):
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    report = build_divergence_report(session)  # 外部实现不存在
    assert report.status == "gap"
    assert report.browser_states == 4
    assert "not fabricated" in report.detail
    out = json.loads((session / "derived" / "vm-divergence.json").read_text())
    assert out["status"] == "gap" and out["browser_states"] == 4


def test_divergence_report_gap_without_browser_trace(tmp_path):
    session = make_session(tmp_path)  # 无 vm-loop
    ext = tmp_path / "ext.json"
    ext.write_text(json.dumps({"states": [33, 14]}))
    report = build_divergence_report(session, ext)
    assert report.status == "gap"
    assert "browser dispatch trace missing" in report.detail


def test_browser_sequence_follows_streamed_states_file(tmp_path):
    """第十阶段 E1 兼容：states 拆旁车 NDJSON 后按 states_file 指针读。"""
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    vm_dir = session / "raw" / "vm-loop"
    # 把 dispatch 循环的 states 搬到旁车 NDJSON，主 JSON 留指针
    main = json.loads((vm_dir / "trace1.json").read_text())
    target = next(l for l in main["loops"] if l["loop"] == "Lbbb_99")
    states = target.pop("states")
    sidecar_rel = "raw/vm-loop/trace1-states.ndjson"
    lines = [json.dumps({"loop": "Lbbb_99", "realm": "frame[0]",
                         "i": i, "state": s}) for i, s in enumerate(states)]
    (vm_dir / "trace1-states.ndjson").write_text("\n".join(lines) + "\n")
    target["states_file"] = sidecar_rel
    main["states_streamed"] = {"file": sidecar_rel, "states": len(states)}
    (vm_dir / "trace1.json").write_text(json.dumps(main))

    states_out, artifact = browser_state_sequence(session)
    assert states_out == [33, 14, 4, 70]  # 与内联形态一致
    assert artifact and "vm-loop" in artifact


def test_divergence_report_diverged_with_external_file(tmp_path):
    session = make_session(tmp_path)
    add_trace_fixtures(session)
    ext = tmp_path / "ext.json"
    ext.write_text(json.dumps([33, 14, 4, 99]))
    report = build_divergence_report(session, ext)
    assert report.status == "diverged"
    assert report.first_divergence["index"] == 3
    assert report.first_divergence["browser"] == 70
    assert report.artifacts["external_trace"].endswith("ext.json")
    out = json.loads((session / "derived" / "vm-divergence.json").read_text())
    assert out["status"] == "diverged"


def test_load_external_states_rejects_bad_shape(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps("nope"))
    try:
        load_external_states(bad)
        raise AssertionError("should reject")
    except ValueError:
        pass
