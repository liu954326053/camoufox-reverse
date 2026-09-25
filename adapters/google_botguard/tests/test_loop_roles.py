"""Fixture-driven tests for loop role classification (offline, no browser).

Signatures distilled from live session 266b076a (dola Google login, reverse.9):
- string-decoder: tD charCodeAt loop, fixed 5-state FSM period,
  iterations/period ≈ VM program byte count;
- decompressor: body defines a bit-stream reader `for(;z<n;)D|=f(a)<<z,z+=8`
  and reads a (P(3)+1, P(5)) header;
- table-permutation: body does modulo-index sampling `h=(h+k)%u,Y[f](D[h])`;
- cipher: body pushes XOR-stream bytes `Z.push(Z.rA[X&l]^e)`;
- unknown: none of the above.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.google_botguard.loop_roles import classify_artifact, classify_loop

# --- 真实源码片段（复测轮 266b076a，8b23cc1e 动态脚本），各以循环头开头 ---

DECODER_SRC = ("tD=function(x,l,S,u,C,b,z,w,V,f,e){{f=33;while(f!=30)if(f==92)"
               "f=V<l.length?8:95;else if(f==8)w=l.charCodeAt(V),f=11;"
               "else if(f==35)(z[b++]=w,f=20);else if(f==20&&(V++,f=92))}}")
# loop offset = DECODER_SRC.index("while")
DECOMPRESSOR_SRC = ("F==76?(P=function(gj,nA){for(;z<gj;)D|=sR(a)<<z,z+=8;"
                    "return z-=(nA=D&(1<<gj)-1,gj),D>>=gj,nA},N=w(K,66),z=D=0,"
                    "u=(P(3)|0)+1,b=P(5),I=0,Y=[],C=0,F=45):while(F!=43)F==41?"
                    "(Y[kr]&&(XZ[kr]=A(a,27)),F=24):F==24?(kr++,F=40):F==8?"
                    "F=I<b?37:10:F==10&&(F=43)")
PERM_SRC = ("g=function(a,u,C,D,Y,h,b,O){for(O=50;O!=20;)O==79?O=29:O==82?"
            "(h=((h|0)+(TN(K,128,12)|0))%u,Y[f](D[h]),O=70):O==97?(Du(K,b,Y),"
            "O=20):O==50?(b=A(a,6),C=TN(G,128,10),Y=[],D=t(211,S),u=D[r],h=0,"
            "O=79):O==29?O=C--?82:97:O==70&&(O=29)}")
CIPHER_SRC = ("k=function(Z){for(T=7,K=36;;)try{if(T==29)break;else if(T==91)"
              "K=67,Z.rA=Wc(8,0,A(24,56,Z,4),A(24,42,Z,3),n),T=35;else{if(T==86)"
              "throw K=36,m;T==35&&(Z.push(Z.rA[X&l]^e),T=29)}}catch(g){}}")
PLAIN_SRC = "function p(){var i=0,s=0;while(i<10){s+=i;i++;}return s;}"
BITREAD_SRC = ("br=function(l,G){for(Q=8,G=c;G>0;)v=k%8,e=k>>3,X=l.j4[e],"
               "X^=l.c6[e&Q],n|=(X>>8-(v|0)-(M|0)&(1<<M)-1)<<(G|0)-(M|0),"
               "k+=M,G-=M;return n}")
ROUND_SRC = ("rc=function(l,p,c,q,k,M,B,V){for(M=(B=(V=p,k[3]|p),k[2]|p);V<16;V++)"
             "B=B>>>l|B<<24,B+=M|p,B^=V+3641,c=c>>>l|c<<24,c+=q|p,q=q<<3|q>>>29,"
             "c^=M+3641,M=M<<3|M>>>29,M^=B,q^=c;return[q>>>24&255]}")
THUNK_SRC = ("M=function(l){while(0==![]){return l;if({})break}}")


def _periodic_states(period_vals, cycles):
    return [[v] for v in period_vals * cycles]


def make_artifact():
    """合成 artifact：五个循环各挂一段真实签名源码。"""
    loops = [
        {"loop": "Laaa_%d" % DECODER_SRC.index("while"), "iterations": 148428,
         "states_recorded": 10000, "truncated": True,
         "states": _periodic_states([92, 8, 11, 35, 20], 2000)},
        {"loop": "Lbbb_%d" % DECOMPRESSOR_SRC.index("while"), "iterations": 775,
         "states_recorded": 775, "truncated": False,
         "states": [[i % 97] for i in range(775)]},
        {"loop": "Lccc_%d" % PERM_SRC.index("for"), "iterations": 56893,
         "states_recorded": 10000, "truncated": True,
         "states": [[v] for v in [79, 29, 82, 70] * 2500]},
        {"loop": "Lddd_%d" % CIPHER_SRC.index("for"), "iterations": 8722,
         "states_recorded": 8722, "truncated": False,
         "states": [[v] for v in [7, 49, 96, 91, 35] * 1744 + [7, 49]]},
        {"loop": "Leee_%d" % PLAIN_SRC.index("while"), "iterations": 11,
         "states_recorded": 11, "truncated": False,
         "states": [[i] for i in range(11)]},
        {"loop": "Lfff_%d" % BITREAD_SRC.index("for"), "iterations": 88134,
         "states_recorded": 0, "truncated": False, "states": []},
        {"loop": "Lggg_%d" % ROUND_SRC.index("for"), "iterations": 66691,
         "states_recorded": 0, "truncated": False, "states": []},
        {"loop": "Lhhh_%d" % THUNK_SRC.index("while"), "iterations": 29956,
         "states_recorded": 0, "truncated": False, "states": []},
    ]
    sources = [
        {"hash": "aaa", "kind": "tt-createScript", "source": DECODER_SRC},
        {"hash": "bbb", "kind": "tt-createScript", "source": DECOMPRESSOR_SRC},
        {"hash": "ccc", "kind": "tt-createScript", "source": PERM_SRC},
        {"hash": "ddd", "kind": "tt-createScript", "source": CIPHER_SRC},
        {"hash": "eee", "kind": "tt-createScript", "source": PLAIN_SRC},
        {"hash": "fff", "kind": "tt-createScript", "source": BITREAD_SRC},
        {"hash": "ggg", "kind": "tt-createScript", "source": ROUND_SRC},
        {"hash": "hhh", "kind": "tt-createScript", "source": THUNK_SRC},
    ]
    return {"loops": loops, "dynamic_sources": sources}


class TestClassifyLoop:
    def test_string_decoder_by_period_and_program_size(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art, program_size=29627)}
        r = roles["Laaa_%d" % DECODER_SRC.index("while")]
        assert r.role == "string-decoder"
        assert r.evidence == "observed"
        assert "charCodeAt" in r.rationale

    def test_decompressor_by_bitreader_signature(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Lbbb_%d" % DECOMPRESSOR_SRC.index("while")]
        assert r.role == "decompressor"
        assert r.evidence == "observed"

    def test_table_permutation_by_modulo_sampling(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Lccc_%d" % PERM_SRC.index("for")]
        assert r.role == "table-permutation"
        assert r.evidence == "observed"

    def test_cipher_by_xor_push(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Lddd_%d" % CIPHER_SRC.index("for")]
        assert r.role == "cipher"
        assert r.evidence == "observed"

    def test_unknown_with_histogram(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Leee_%d" % PLAIN_SRC.index("while")]
        assert r.role == "unknown"
        assert r.evidence == "gap"
        assert r.state_histogram  # 附状态直方图供人工判读

    def test_decoder_without_program_size_falls_back(self):
        """没有程序体量时，周期 FSM 只能给 inferred 的 periodic-fsm，不得冒充 decoder。"""
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art, program_size=None)}
        r = roles["Laaa_%d" % DECODER_SRC.index("while")]
        assert r.role == "periodic-fsm"
        assert r.evidence == "inferred"

    def test_prologue_tolerated(self):
        """FSM 初始化 prologue（真实 session：前 5 个状态一次性）不破坏周期判定。"""
        art = make_artifact()
        lid = "Laaa_%d" % DECODER_SRC.index("while")
        for l in art["loops"]:
            if l["loop"] == lid:
                l["states"] = [[v] for v in [17, 89, 10, 32, 67]] + \
                              _periodic_states([58, 74, 41, 33, 67], 1999)
        roles = {r.loop: r for r in classify_artifact(art, program_size=29627)}
        r = roles[lid]
        assert r.role == "string-decoder"
        assert r.period == 5

    def test_bit_reader_signature(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Lfff_%d" % BITREAD_SRC.index("for")]
        assert r.role == "bit-reader"
        assert r.evidence == "observed"

    def test_rotation_cipher_signature(self):
        """无 push 的旋转移位轮函数也归 cipher。"""
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Lggg_%d" % ROUND_SRC.index("for")]
        assert r.role == "cipher"
        assert "轮转" in r.rationale or "旋转" in r.rationale

    def test_string_accessor_thunk(self):
        art = make_artifact()
        roles = {r.loop: r for r in classify_artifact(art)}
        r = roles["Lhhh_%d" % THUNK_SRC.index("while")]
        assert r.role == "string-accessor"
        assert r.evidence == "observed"


class TestArtifactIo:
    def test_write_loop_roles(self, tmp_path: Path):
        session = tmp_path / "runs" / "s1"
        (session / "raw" / "vm-loop").mkdir(parents=True)
        (session / "derived").mkdir(parents=True)
        (session / "raw" / "vm-loop" / "a1.json").write_text(
            json.dumps(make_artifact()))
        from adapters.google_botguard.loop_roles import write_loop_roles
        out = write_loop_roles(session)
        data = json.loads((session / "derived" / "loop-roles.json").read_text())
        assert out == session / "derived" / "loop-roles.json"
        assert len(data["loops"]) == 8
        roles = {l["loop"]: l["role"] for l in data["loops"]}
        assert "decompressor" in roles.values()
        assert "table-permutation" in roles.values()

    def test_missing_artifact_is_gap_not_crash(self, tmp_path: Path):
        session = tmp_path / "runs" / "s2"
        (session / "derived").mkdir(parents=True)
        from adapters.google_botguard.loop_roles import write_loop_roles
        out = write_loop_roles(session)
        data = json.loads((session / "derived" / "loop-roles.json").read_text())
        assert data["evidence"] == "gap"
