"""Regressions for request identity, raw evidence and navigation barriers."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import navigation, network
from tests.fakes import make_fake_runtime


class Request:
    url = "https://fixture.invalid/api"
    method = "POST"
    resource_type = "fetch"
    headers = {"authorization": "fixture-secret"}
    post_data = "same-body"
    post_data_buffer = b"same-body"
    redirected_from = None

    def __init__(self, frame):
        self.frame = frame


@pytest.mark.asyncio
async def test_out_of_order_same_url_responses_use_request_identity(tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")
    manager._capturing = True
    frame = object()
    first, second = Request(frame), Request(frame)
    manager._on_request(first)
    manager._on_request(second)
    manager._on_response_async(SimpleNamespace(request=first, url=first.url, status=201, headers={}))
    manager._on_response_async(SimpleNamespace(request=second, url=second.url, status=202, headers={}))
    assert [r["status"] for r in manager._network_requests] == [201, 202]


@pytest.mark.asyncio
async def test_initiator_never_falls_back_to_page_url_search(monkeypatch):
    manager = BrowserManager()
    manager._network_requests.append({"id": 7, "url": Request.url})
    monkeypatch.setattr(network, "browser_manager", manager)
    result = await network.get_request_initiator(7)
    assert result.get("source") == "unknown"
    assert result.get("initiator_stack") is None
    assert result.get("diagnostics", {}).get("correlation") == "unmatched"


@pytest.mark.asyncio
async def test_navigation_write_failure_blocks_goto_and_buffer_clear(monkeypatch, tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")
    manager._capturing = True
    manager._on_request(Request(object()))
    navigations = []

    async def goto(*args, **kwargs):
        navigations.append(args)
        return SimpleNamespace(status=200)

    async def title():
        return "fixture"

    manager.pages["default"].goto = goto
    manager.pages["default"].title = title

    def failed_write(*args, **kwargs):
        raise OSError("fixture disk full")

    manager.runtime.write_artifact = failed_write
    monkeypatch.setattr(navigation, "browser_manager", manager)
    result = await navigation.navigate("https://fixture.invalid/next")
    assert "error" in result
    assert navigations == []
    assert len(manager._network_requests) == 1


@pytest.mark.asyncio
async def test_capture_clear_never_reuses_request_ids(monkeypatch, tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")
    manager._capturing = True
    frame = object()
    manager._on_request(Request(frame))
    first_id = manager._network_requests[0]["id"]
    monkeypatch.setattr(network, "browser_manager", manager)
    await network.network_capture("clear")
    manager._on_request(Request(frame))
    assert manager._network_requests[0]["id"] > first_id


@pytest.mark.asyncio
async def test_stuck_frame_checkpoint_is_bounded_and_persists_gap(tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")

    class StuckFrame:
        async def evaluate(self, _script):
            await asyncio.Event().wait()

    class Page:
        frames = [StuckFrame()]

        def is_closed(self):
            return False

    class Context:
        pages = [Page()]

    evidence = manager.network_evidence
    evidence.contexts.add(Context())
    evidence.checkpoint_timeout = 0.03
    await asyncio.wait_for(evidence.checkpoint(), timeout=0.25)
    gaps = list((manager.session.path / "raw/mcp-network/gaps").rglob("*.json"))
    assert len(gaps) == 1
    assert json.loads(gaps[0].read_text())["reason"] == "frame_checkpoint_timeout"
    closed = await manager.close()
    assert closed["status"] == "incomplete"


@pytest.mark.asyncio
async def test_pending_response_checkpoint_keeps_raw_event_and_gap(tmp_path):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")

    class PendingFrame:
        async def evaluate(self, _script):
            return {"events": [{"type": "fetch", "document_id": "doc", "call_id": "one",
                                "revision": 1, "body_ready": True, "body": "raw-fixture",
                                "stack": "streamingCall@fixture:1"}], "pending": 1}

    class Page:
        frames = [PendingFrame()]

        def is_closed(self):
            return False

    class Context:
        pages = [Page()]

    manager.network_evidence.contexts.add(Context())
    await manager.network_evidence.checkpoint()
    calls = list((manager.session.path / "raw/mcp-network/calls").rglob("*.json"))
    assert len(calls) == 1
    assert json.loads(calls[0].read_text())["body"] == "raw-fixture"
    gaps = list((manager.session.path / "raw/mcp-network/gaps").rglob("*.json"))
    assert json.loads(gaps[0].read_text())["pending"] == 1
