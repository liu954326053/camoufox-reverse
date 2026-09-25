"""handler_patch.py 的契约测试（第四阶段 Task 5）。

补丁正则在 node/浏览器端以 JS RegExp 执行；这里用 Python re 模拟
（$& 与 $N 分别转为 \\g<0> 与 \\N 捕获组引用）验证补丁逻辑本身，
通道行为由 node 用例覆盖。
"""
from __future__ import annotations

import json
import re

from adapters.google_botguard.handler_patch import (
    COUNTER_GLOBAL,
    handler_count_patches,
    write_handler_counts,
)


def _js_replace(src: str, patch: dict) -> str:
    """模拟 JS String.replace(RegExp, replacement) 的 $&/$N 语义。"""
    replacement = patch["replacement"]
    replacement = replacement.replace("$&", r"\g<0>")
    replacement = re.sub(r"\$(\d)", r"\\\1", replacement)
    return re.sub(patch["pattern"], replacement, src)


# 与 2026-09-25 build 实测形态一致（test_dispatch.py docstring）：
# registrar 与 dispatch 均为赋值形态，且 t 是三元函数。
FIXTURE = (
    "Du=function(l,p,c){l.Y[p]=TN(p,43,3,c,113,l);};"
    "t=function(l,p,c){c=p.Y[l];c.create();};"
)


def test_patches_match_assignment_form_real_signature():
    patches = handler_count_patches()
    out = _js_replace(FIXTURE, patches[0])
    assert COUNTER_GLOBAL in out
    assert ".registered[p]=" in out  # opcode id 参数 p 被捕获组展开
    out2 = _js_replace(FIXTURE, patches[1])
    assert ".dispatched[l]=" in out2


def test_patches_match_function_declaration_form():
    src = "function Du(l,p,c){l.Y[p]=c;}function t(l,p,c){var x=p.Y[l];x.create();}"
    for patch in handler_count_patches():
        assert COUNTER_GLOBAL in _js_replace(src, patch)


def test_unmatched_patch_leaves_source_untouched():
    renamed = "function Xq(l,p,c){l.Y[p]=c;}"
    assert _js_replace(renamed, handler_count_patches()[0]) == renamed


def test_custom_names_for_new_build():
    patches = handler_count_patches(register_fn="Xq", dispatch_fn="Zk")
    assert "Xq" in patches[0]["pattern"] and "Zk" in patches[1]["pattern"]
    src = "function Xq(a,b,c){a.Y[b]=c;}"
    assert COUNTER_GLOBAL in _js_replace(src, patches[0])


def test_write_handler_counts_merges_artifacts(tmp_path):
    vm_loop = tmp_path / "raw" / "vm-loop"
    vm_loop.mkdir(parents=True)
    (vm_loop / "a.json").write_text(json.dumps({
        "counters": {"registered": {"232": 2, "140": 1},
                     "dispatched": {"232": 5}}}))
    (vm_loop / "b.json").write_text(json.dumps({
        "counters": {"registered": {"232": 1}, "dispatched": {"140": 3}}}))
    (vm_loop / "c.json").write_text(json.dumps({"counters": None}))
    out = write_handler_counts(tmp_path)
    payload = json.loads(out.read_text())
    assert payload["registered"] == {"232": 3, "140": 1}
    assert payload["dispatched"] == {"232": 5, "140": 3}
    assert payload["artifacts_with_counters"] == 2
    assert payload["artifacts_scanned"] == 3
    assert payload["level"] == "observed"


def test_write_handler_counts_gap_when_no_counters(tmp_path):
    (tmp_path / "raw" / "vm-loop").mkdir(parents=True)
    (tmp_path / "raw" / "vm-loop" / "a.json").write_text(json.dumps({}))
    out = write_handler_counts(tmp_path)
    payload = json.loads(out.read_text())
    assert payload["level"] == "gap"
    assert payload["registered"] == {}
