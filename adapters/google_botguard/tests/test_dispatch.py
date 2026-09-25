"""Fixture-driven tests for dispatch form verdict (offline).

Real-code signatures from session 266b076a (8b23cc1e dynamic script):
- registrar: Du=function(l,p,c){...l.Y[p]=TN(p,43,3,c,113,l)...}
- dispatch: t=function(l,p,c){c=p.Y[l];...c.create()...}
- scope stack: M.TY.push(M.Y.slice())
- no centralized giant switch (max observed: 31 cases, DOM utility).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.google_botguard.dispatch import verdict_for_source, write_verdict

# 真实形态缩样：注册器 + 派发器 + 作用域栈
TABLE_SRC = (
    "Du=function(l,p,c){if(p==140||p==170)l.Y[p]?l.Y[p].concat(c):"
    "l.Y[p]=w(c,14,l);else{l.Y[p]=TN(p,43,3,c,113,l)}"
    "p==232&&(l.W=bC(l,false,32),l.L=void 0)},"
    "t=function(l,p,c){if((c=p.Y[l],c)===void 0)throw[30,l];return c.create()},"
    "kd=function(l,p,c,q,k,M){M.TY.push(M.Y.slice()),M.Y[q]=void 0,Du(M,q,c)},"
    "E(26,function(a,u){q7(1,a)},(l.xL=(E(29,function(a){A(a,3)},393,l))),0),"
    "E(27,function(a,u,C,D,Y,h,b,O){for(O=50;O!=20;)O==82?(h=((h|0)+1)%u,"
    "Y[f](D[h]),O=70):O==97?(Du(K,b,Y),O=20):O==50?(Y=[],O=79):O==79?O=29:"
    "O==29?O=82:O==70&&(O=29)},48,l)"
)

CENTRALIZED_SRC = (
    "vm=function(p){var pc=0,op=0;while(true){op=p[pc++];switch(op){"
    + "".join(f"case {i}:r[{i}%8]=r[{i}%8]+{i};break;" for i in range(80))
    + "default:return;}}}"
)

PLAIN_SRC = "function add(a,b){return a+b;}"


class TestVerdictForSource:
    def test_table_indirect(self):
        v = verdict_for_source(TABLE_SRC)
        assert v["form"] == "table-indirect"
        kinds = [e["kind"] for e in v["evidence"]]
        assert "handler-table-registrar" in kinds
        assert "create-dispatch" in kinds
        assert "scope-stack" in kinds
        assert all(e["level"] == "observed" for e in v["evidence"])

    def test_centralized_switch(self):
        v = verdict_for_source(CENTRALIZED_SRC)
        assert v["form"] == "centralized"
        assert any(e["kind"] == "giant-switch" for e in v["evidence"])

    def test_unknown_when_no_signal(self):
        v = verdict_for_source(PLAIN_SRC)
        assert v["form"] == "unknown"
        assert v["evidence"] == []

    def test_table_beats_nothing_even_with_small_switch(self):
        src = TABLE_SRC + "f=function(x){switch(x){case 1:break;case 2:break;}}"
        v = verdict_for_source(src)
        assert v["form"] == "table-indirect"


class TestWriteVerdict:
    def _session(self, tmp_path: Path, sources) -> Path:
        session = tmp_path / "runs" / "s1"
        (session / "raw" / "vm-loop").mkdir(parents=True)
        (session / "derived").mkdir(parents=True)
        artifact = {"loops": [], "dynamic_sources": [
            {"hash": "h%d" % i, "kind": "tt-createScript", "source": s}
            for i, s in enumerate(sources)]}
        (session / "raw" / "vm-loop" / "a1.json").write_text(json.dumps(artifact))
        return session

    def test_write_verdict_observed(self, tmp_path: Path):
        session = self._session(tmp_path, [TABLE_SRC, PLAIN_SRC])
        out = write_verdict(session)
        data = json.loads((session / "derived" / "dispatch-verdict.json").read_text())
        assert out == session / "derived" / "dispatch-verdict.json"
        assert data["form"] == "table-indirect"
        assert data["max_switch_cases"] == 0

    def test_no_sources_is_gap(self, tmp_path: Path):
        session = tmp_path / "runs" / "s2"
        (session / "derived").mkdir(parents=True)
        write_verdict(session)
        data = json.loads((session / "derived" / "dispatch-verdict.json").read_text())
        assert data["form"] == "unknown"
        assert data["evidence"] == "gap"
