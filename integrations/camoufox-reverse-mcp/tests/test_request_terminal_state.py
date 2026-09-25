"""第十二阶段：请求终态自描述回归测试（terminal_state / failure_reason / pending_at_close）。"""
import json
from types import SimpleNamespace

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import network
from tests.fakes import make_fake_runtime


class Request:
    url = "https://fixture.invalid/sensor"
    method = "POST"
    resource_type = "fetch"
    headers = {"content-type": "application/json"}
    post_data = "payload"
    post_data_buffer = b"payload"
    redirected_from = None

    def __init__(self, frame, failure=None):
        self.frame = frame
        self.failure = failure


async def _launched_capturing_manager(tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")
    manager._capturing = True
    return manager


@pytest.mark.asyncio
async def test_requestfailed_records_terminal_state_and_reason(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    req = Request(object(), failure="net::ERR_ABORTED")
    manager._on_request(req)
    manager._on_request_failed(req)
    entry = manager._network_requests[0]
    assert entry["terminal_state"] == "failed"
    assert entry["failure_reason"] == "net::ERR_ABORTED"
    assert entry["status"] is None  # 中止请求没有响应状态码，不伪造
    assert entry["duration"] is not None


@pytest.mark.asyncio
async def test_requestfailed_without_reason_is_unknown(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    req = Request(object(), failure=None)
    manager._on_request(req)
    manager._on_request_failed(req)
    entry = manager._network_requests[0]
    assert entry["terminal_state"] == "failed"
    assert entry["failure_reason"] == "unknown"  # 拿不到原因记 unknown，不编造


@pytest.mark.asyncio
async def test_requestfailed_for_uncaptured_request_is_noop(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request_failed(Request(object(), failure="net::ERR_ABORTED"))
    assert len(manager._network_requests) == 0


@pytest.mark.asyncio
async def test_answered_request_terminal_state(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    req = Request(object())
    manager._on_request(req)
    assert manager._network_requests[0]["terminal_state"] == "pending"
    manager._on_response_async(
        SimpleNamespace(request=req, url=req.url, status=200, headers={})
    )
    entry = manager._network_requests[0]
    assert entry["terminal_state"] == "answered"
    assert entry["failure_reason"] is None
    assert entry["pending_at_close"] is False


@pytest.mark.asyncio
async def test_capture_stop_marks_only_pending_requests(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    answered = Request(object())
    pending = Request(object())
    manager._on_request(answered)
    manager._on_request(pending)
    manager._on_response_async(
        SimpleNamespace(request=answered, url=answered.url, status=200, headers={})
    )
    monkeypatch.setattr(network, "browser_manager", manager)
    result = await network.network_capture("stop")
    assert result["status"] == "stopped"
    assert result["pending_at_close"] == 1
    by_terminal = {e["terminal_state"]: e for e in manager._network_requests}
    assert by_terminal["pending"]["pending_at_close"] is True
    assert by_terminal["answered"]["pending_at_close"] is False
    # 标记不升级终态：在途仍是在途，不伪装成 failed
    assert by_terminal["pending"]["failure_reason"] is None


@pytest.mark.asyncio
async def test_close_persists_pending_at_close_evidence(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    req = Request(object())
    manager._on_request(req)
    closed = await manager.close()
    assert closed["status"] == "closed"
    artifacts = list((manager._last_close_result and tmp_path.joinpath(
        "project/runs/session-tools/raw/mcp-network/requests").rglob("*.json")) or [])
    assert artifacts, "expected persisted request evidence"
    payloads = [json.loads(p.read_text()) for p in artifacts]
    assert any(p.get("pending_at_close") is True and p.get("terminal_state") == "pending"
               for p in payloads)
    assert len(manager._network_requests) == 0  # 缓冲区随关闭清空，证据已落盘


@pytest.mark.asyncio
async def test_list_network_requests_exposes_terminal_fields(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    failed = Request(object(), failure="net::ERR_CONNECTION_RESET")
    pending = Request(object())
    manager._on_request(failed)
    manager._on_request(pending)
    manager._on_request_failed(failed)
    monkeypatch.setattr(network, "browser_manager", manager)
    summaries = await network.list_network_requests()
    assert len(summaries) == 2
    failed_summary = next(s for s in summaries if s["failure_reason"] == "net::ERR_CONNECTION_RESET")
    assert failed_summary["terminal_state"] == "failed"
    pending_summary = next(s for s in summaries if s["terminal_state"] == "pending")
    assert pending_summary["failure_reason"] is None
    assert pending_summary["pending_at_close"] is False
