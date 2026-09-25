"""第四阶段 Task 4：chrome 噪音过滤的契约测试。

trace jsonl 中 o=="script" 的 enter/exit 事件会混入浏览器内部模块
（resource://gre/modules/*.sys.mjs 等）。raw 只追加，过滤产出 derived 视图。
"""

import json

from camoufox.trace_filter import (
    NOISE_PREFIXES,
    filter_lines,
    filter_trace,
    is_noise,
)


def _line(obj):
    return json.dumps(obj, ensure_ascii=False)


SCRIPT_ENTER = {"o": "script", "p": "resource://gre/modules/AppConstants.sys.mjs",
                "v": "line:1", "t": 232, "k": 3, "u": 232340, "q": 0,
                "s": "script.exec@js/src/vm/Interpreter.cpp"}
SCRIPT_EXIT = dict(SCRIPT_ENTER, k=4, q=1)
DOM_EVENT = {"o": "window", "p": "innerWidth", "v": "", "t": 222, "k": 0,
             "q": 0, "s": "window.innerWidth@dom/base/nsGlobalWindowInner.cpp"}
WEB_SCRIPT = {"o": "script",
              "p": "https://accounts.google.com/v3/signin/_/botguard.js",
              "v": "line:1", "t": 500, "k": 3, "q": 99,
              "s": "script.exec@js/src/vm/Interpreter.cpp"}


def test_resource_chrome_about_prefixes_are_noise():
    for prefix in NOISE_PREFIXES:
        event = dict(WEB_SCRIPT, p=f"{prefix}internal/module.js")
        assert is_noise(event), prefix


def test_web_scripts_and_dom_events_are_not_noise():
    assert not is_noise(WEB_SCRIPT)
    assert not is_noise(DOM_EVENT)
    assert not is_noise({"o": "script", "p": "blob:https://dola.com/uuid",
                         "k": 3})


def test_filter_lines_keeps_order_and_counts_per_prefix():
    lines = [
        _line(SCRIPT_ENTER),
        _line(DOM_EVENT),
        _line(dict(WEB_SCRIPT)),
        _line(dict(SCRIPT_EXIT, p="chrome://browser/content/abc.js")),
        _line(dict(SCRIPT_EXIT, p="about:blank")),
    ]
    kept, stats = filter_lines(lines)
    kept_events = [json.loads(line) for line in kept]
    assert kept_events == [DOM_EVENT, WEB_SCRIPT]
    assert stats["total"] == 5
    assert stats["kept"] == 2
    assert stats["filtered"] == 3
    assert stats["by_prefix"] == {"resource://": 1, "chrome://": 1, "about:": 1}


def test_filter_lines_tolerates_malformed_json():
    kept, stats = filter_lines(["not json", _line(DOM_EVENT)])
    assert kept == ["not json", _line(DOM_EVENT)]
    assert stats["unparseable"] == 1
    assert stats["kept"] == 2
    assert stats["filtered"] == 0


def test_filter_trace_writes_derived_file_and_stats(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join([_line(SCRIPT_ENTER), _line(WEB_SCRIPT),
                              _line(SCRIPT_EXIT)]) + "\n", encoding="utf-8")
    dst = tmp_path / "out.jsonl"
    stats = filter_trace(src, dst)
    assert stats["filtered"] == 2
    out_events = [json.loads(line)
                  for line in dst.read_text(encoding="utf-8").splitlines()]
    assert out_events == [WEB_SCRIPT]
    # 输入文件不得被修改（raw 只追加纪律）
    assert len(src.read_text(encoding="utf-8").splitlines()) == 3
