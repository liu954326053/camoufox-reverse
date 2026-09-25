"""Fixture-driven tests for opcode hypothesis aggregation (offline)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.google_botguard.opcode_report import build_hypotheses, write_report


def _derived_session(tmp_path: Path, roles=None, verdict=None,
                     program_size=None) -> Path:
    session = tmp_path / "runs" / "s1"
    (session / "derived").mkdir(parents=True)
    if roles is not None:
        (session / "derived" / "loop-roles.json").write_text(json.dumps(roles))
    if verdict is not None:
        (session / "derived" / "dispatch-verdict.json").write_text(
            json.dumps(verdict))
    return session


ROLES = {
    "evidence": "observed", "program_size": 29795,
    "role_counts": {"string-decoder": 2, "cipher": 9},
    "loops": [
        {"loop": "Laaa_100", "iterations": 148910, "role": "string-decoder",
         "evidence": "observed", "rationale": "周期×5≈程序字节数", "period": 5},
        {"loop": "Lbbb_200", "iterations": 56893, "role": "table-permutation",
         "evidence": "observed", "rationale": "取模采样"},
        {"loop": "Lccc_300", "iterations": 88134, "role": "bit-reader",
         "evidence": "observed", "rationale": "位掩码累积"},
        {"loop": "Lddd_400", "iterations": 12, "role": "unknown",
         "evidence": "gap", "rationale": "无签名",
         "state_histogram": {"1": 5, "2": 7}},
    ],
}
VERDICT = {"form": "table-indirect", "evidence": [
    {"kind": "handler-table-registrar", "level": "observed", "detail": "Y[p]"},
    {"kind": "create-dispatch", "level": "observed", "detail": ".create()"},
]}


class TestBuildHypotheses:
    def test_roles_carry_semantics_with_levels(self):
        h = build_hypotheses(ROLES, VERDICT, program_size=29795)
        sems = {x["loop"]: x for x in h["loop_semantics"]}
        # observed 角色的语义断言
        assert sems["Laaa_100"]["semantics"] == "字符串解码（逐字符 FSM）"
        assert sems["Laaa_100"]["evidence"] == "observed"
        assert sems["Lbbb_200"]["semantics"] == "handler 表置换/采样"
        assert sems["Lccc_300"]["semantics"] == "程序位流变长读取/解密"
        # unknown 不得编造语义
        assert sems["Lddd_400"]["semantics"] is None
        assert sems["Lddd_400"]["evidence"] == "gap"

    def test_dispatch_section(self):
        h = build_hypotheses(ROLES, VERDICT, program_size=29795)
        assert h["dispatch"]["form"] == "table-indirect"
        assert len(h["dispatch"]["evidence"]) == 2

    def test_handler_semantics_inferred_not_observed(self):
        """opcode handler 的具体语义只能 inferred/gap，禁止 observed。"""
        h = build_hypotheses(ROLES, VERDICT, program_size=29795)
        for hs in h["handler_hypotheses"]:
            assert hs["evidence"] in ("inferred", "gap")

    def test_missing_inputs_are_gap(self):
        h = build_hypotheses(None, None, program_size=None)
        assert h["evidence"] == "gap"

    def test_known_opcodes_from_registrar(self):
        """注册器里可确认的特殊 id（232=密钥流重置）列为代表性 handler 假设。"""
        h = build_hypotheses(ROLES, VERDICT, program_size=29795)
        ids = {x["opcode_id"] for x in h["handler_hypotheses"]}
        assert 232 in ids
        op232 = next(x for x in h["handler_hypotheses"] if x["opcode_id"] == 232)
        assert "密钥流" in op232["hypothesis"]


class TestWriteReport:
    def test_writes_derived_json(self, tmp_path: Path):
        session = _derived_session(tmp_path, ROLES, VERDICT)
        out = write_report(session, program_size=29795)
        data = json.loads((session / "derived" / "opcode-hypotheses.json").read_text())
        assert out == session / "derived" / "opcode-hypotheses.json"
        assert data["evidence"] == "observed"
        assert len(data["loop_semantics"]) == 4

    def test_missing_derived_inputs(self, tmp_path: Path):
        session = _derived_session(tmp_path)
        write_report(session)
        data = json.loads((session / "derived" / "opcode-hypotheses.json").read_text())
        assert data["evidence"] == "gap"
