"""第十二阶段：引擎层 initiator 栈（engine_initiator_stack）离线回归测试。

覆盖：driver initializer 透出字段的读取、不可用情形记 None + 计数自描述、
list/get/get_request_initiator 输出形态、与页面 hook initiator 并存不互覆、
driver 补丁模块的幂等性与锚点唯一性。
"""
import json
from types import SimpleNamespace

import pytest

from camoufox_reverse_mcp._playwright_initiator_patch import (
    _RULES,
    _apply_rule,
    patch_playwright_initiator_stack,
)
from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import network
from tests.fakes import make_fake_runtime


ENGINE_STACK = [
    {"functionName": "levelOneFetch", "filename": "http://fixture.invalid/",
     "lineNumber": 7, "columnNumber": 10},
    {"functionName": "levelTwoFetch", "filename": "http://fixture.invalid/",
     "lineNumber": 9, "columnNumber": 35},
]


class Request:
    url = "https://fixture.invalid/api/beacon"
    method = "POST"
    resource_type = "fetch"
    headers = {"content-type": "application/json"}
    post_data = "payload"
    post_data_buffer = b"payload"
    redirected_from = None

    def __init__(self, frame, engine_stack=...):
        self.frame = frame
        if engine_stack is not ...:
            initializer = {"url": self.url}
            if engine_stack is not None:
                initializer["engineInitiatorStack"] = engine_stack
            self._impl_obj = SimpleNamespace(_initializer=initializer)


async def _launched_capturing_manager(tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")
    manager._capturing = True
    return manager


@pytest.mark.asyncio
async def test_engine_stack_recorded_from_driver_initializer(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), engine_stack=ENGINE_STACK))
    entry = manager._network_requests[0]
    assert entry["engine_initiator_stack"] == ENGINE_STACK
    assert manager._engine_initiator_stats == {"captured": 1, "unavailable": 0}


@pytest.mark.asyncio
async def test_engine_stack_unavailable_is_null_and_counted(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), engine_stack=None))  # 键存在但为 None
    manager._on_request(Request(object()))  # 无 _impl_obj（老 driver / 未打补丁）
    for entry in manager._network_requests:
        assert entry["engine_initiator_stack"] is None
    assert manager._engine_initiator_stats == {"captured": 0, "unavailable": 2}


@pytest.mark.asyncio
async def test_engine_stack_malformed_is_null(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), engine_stack=[]))  # 空栈视同不可用
    manager._on_request(Request(object(), engine_stack="not-a-list"))
    assert all(e["engine_initiator_stack"] is None for e in manager._network_requests)
    assert manager._engine_initiator_stats["unavailable"] == 2


@pytest.mark.asyncio
async def test_list_and_get_expose_engine_stack(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), engine_stack=ENGINE_STACK))
    manager._on_request(Request(object()))
    monkeypatch.setattr(network, "browser_manager", manager)

    summaries = await network.list_network_requests()
    with_stack = next(s for s in summaries if s["has_engine_initiator_stack"])
    assert with_stack["engine_initiator_top"] == (
        "levelOneFetch @ http://fixture.invalid/:7")
    without_stack = next(s for s in summaries if not s["has_engine_initiator_stack"])
    assert without_stack["engine_initiator_top"] is None

    detail = await network.get_network_request(with_stack["id"])
    assert detail["engine_initiator_stack"] == ENGINE_STACK


@pytest.mark.asyncio
async def test_get_request_initiator_reports_both_channels(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), engine_stack=ENGINE_STACK))
    monkeypatch.setattr(network, "browser_manager", manager)
    result = await network.get_request_initiator(1)
    # 引擎通道有栈；hook 通道未装 hook，诚实自描述 unmatched，互不覆盖
    assert result["engine_initiator_stack"] == ENGINE_STACK
    assert result["initiator_stack"] is None
    assert result["diagnostics"]["correlation"] == "unmatched"
    assert result["diagnostics"]["engine_initiator_stats"] == {
        "captured": 1, "unavailable": 0}


@pytest.mark.asyncio
async def test_capture_status_reports_engine_stats(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), engine_stack=ENGINE_STACK))
    manager._on_request(Request(object()))
    monkeypatch.setattr(network, "browser_manager", manager)
    result = await network.network_capture("status")
    assert result["engine_initiator_stats"] == {"captured": 1, "unavailable": 1}


# --- driver 补丁模块（离线，临时文件；不碰真实 driver） ---

_SPLIT_FF = (
    "class InterceptableRequest {\n"
    "  constructor(frame, redirectedFrom, payload) {\n"
    "    this.request = new network.Request(frame, payload);\n"
    "    this.request.setRawRequestHeaders(null);\n"
    "  }\n"
    "}\n"
)

_BUNDLED = (
    '          internalCauseToResourceType[payload.internalCause] || causeToResourceType[payload.cause] || "other",\n'
    "          payload.method,\n"
    "          postDataBuffer,\n"
    "          payload.headers\n"
    "        );\n"
    "        this.request.setRawRequestHeaders(null);\n"
    "          isNavigationRequest: request2.isNavigationRequest(),\n"
    "          redirectedFrom: _RequestDispatcher.fromNullable(scope, request2.redirectedFrom())\n"
)


def test_apply_rule_patches_and_is_idempotent(tmp_path):
    target = tmp_path / "ffNetworkManager.js"
    target.write_text(_SPLIT_FF)
    anchor, replacement, marker = next(
        (a, r, m) for n, a, r, m in _RULES if n == "ffNetworkManager.js")
    assert _apply_rule(target, anchor, replacement, marker) is True
    assert "_engineInitiatorStack = payload.initiatorStack || null;" in target.read_text()
    assert _apply_rule(target, anchor, replacement, marker) is False  # 幂等


def test_apply_rule_refuses_ambiguous_anchor(tmp_path):
    target = tmp_path / "coreBundle.js"
    target.write_text(_BUNDLED + "\n" + _BUNDLED)  # 锚点出现两次
    anchor, replacement, marker = next(
        (a, r, m) for n, a, r, m in _RULES if n == "coreBundle.js" and "internalCause" in a)
    assert _apply_rule(target, anchor, replacement, marker) is False
    assert "_engineInitiatorStack" not in target.read_text()  # 不猜，不写


def test_bundled_layout_rules_apply(tmp_path):
    target = tmp_path / "coreBundle.js"
    target.write_text(_BUNDLED)
    for name, anchor, replacement, marker in _RULES:
        if name == "coreBundle.js":
            _apply_rule(target, anchor, replacement, marker)
    text = target.read_text()
    assert "this.request._engineInitiatorStack = payload.initiatorStack || null;" in text
    assert "engineInitiatorStack: request2._engineInitiatorStack || null," in text


def test_patch_entrypoint_never_raises_on_missing_driver(monkeypatch):
    monkeypatch.setattr(
        "camoufox_reverse_mcp._playwright_initiator_patch._driver_lib_root",
        lambda: None)
    patch_playwright_initiator_stack()  # 无 driver 时静默返回，不抛
