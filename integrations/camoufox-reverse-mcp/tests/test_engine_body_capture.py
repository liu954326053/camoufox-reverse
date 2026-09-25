"""第十三阶段：引擎层 fetch/XHR 请求体/响应体捕获（engine_request_body /
engine_response_body）离线回归测试。

覆盖：post_data_buffer 透出为 base64、二进制请求体无损（post_data 解码失败时
引擎字段仍在）、GET 无请求体记 None、超 MAX_BODY_SIZE 截断自描述、响应体
base64 落盘、响应体不可得计数、list/get/capture-status 输出形态。

引擎侧链路（juggler readRequestPostData / ResponseStorage → driver
postDataBuffer / getResponseBody）为上游既有能力，无需 driver 补丁；
这里钉死的是 MCP 层的字段契约。
"""
import base64
from types import SimpleNamespace

import pytest

from camoufox_reverse_mcp.browser import MAX_BODY_SIZE, BrowserManager
from camoufox_reverse_mcp.tools import network
from tests.fakes import make_fake_runtime


class Request:
    url = "https://fixture.invalid/api/beacon"
    method = "POST"
    resource_type = "fetch"
    headers = {"content-type": "application/octet-stream"}
    redirected_from = None

    def __init__(self, frame, body: bytes | None = b"payload"):
        self.frame = frame
        self._body = body
        self._impl_obj = SimpleNamespace(_initializer={"url": self.url})

    @property
    def post_data_buffer(self):
        return self._body

    @property
    def post_data(self):
        # playwright 语义：post_data 按 utf-8 解码，二进制请求体抛 UnicodeDecodeError
        if self._body is None:
            return None
        return self._body.decode("utf-8")


class Response:
    def __init__(self, request, body: bytes = b'{"ok":true}', fail: bool = False):
        self.request = request
        self.status = 200
        self.headers = {"content-type": "application/json"}
        self._body = body
        self._fail = fail

    async def body(self):
        if self._fail:
            raise Exception("Response body is unavailable")
        return self._body


async def _launched_capturing_manager(tmp_path, capture_body=False):
    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch(project_dir=tmp_path / "project")
    manager._capturing = True
    manager._capture_body = capture_body
    return manager


@pytest.mark.asyncio
async def test_engine_request_body_base64_lossless(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), body=b'{"k":"v"}'))
    entry = manager._network_requests[0]
    assert base64.b64decode(entry["engine_request_body"]) == b'{"k":"v"}'
    assert entry["engine_request_body_size"] == 9
    assert entry["engine_request_body_truncated"] is False
    assert manager._engine_body_stats["request_captured"] == 1


@pytest.mark.asyncio
async def test_engine_request_body_binary_survives_post_data_failure(tmp_path):
    """二进制请求体：request_post_data 解码失败记 None，引擎字段 base64 无损。"""
    manager = await _launched_capturing_manager(tmp_path)
    blob = bytes(range(256)) * 4  # 非 utf-8
    manager._on_request(Request(object(), body=blob))
    entry = manager._network_requests[0]
    assert entry["request_post_data"] is None
    assert base64.b64decode(entry["engine_request_body"]) == blob
    assert entry["engine_request_body_size"] == len(blob)


@pytest.mark.asyncio
async def test_engine_request_body_absent_for_bodyless_request(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    manager._on_request(Request(object(), body=None))
    entry = manager._network_requests[0]
    assert entry["engine_request_body"] is None
    assert entry["engine_request_body_size"] is None
    assert entry["engine_request_body_truncated"] is False
    assert manager._engine_body_stats["request_captured"] == 0


@pytest.mark.asyncio
async def test_engine_request_body_truncation_is_self_describing(tmp_path):
    manager = await _launched_capturing_manager(tmp_path)
    big = b"x" * (MAX_BODY_SIZE + 1000)
    manager._on_request(Request(object(), body=big))
    entry = manager._network_requests[0]
    assert entry["engine_request_body_truncated"] is True
    assert entry["engine_request_body_size"] == len(big)
    assert len(base64.b64decode(entry["engine_request_body"])) == MAX_BODY_SIZE


@pytest.mark.asyncio
async def test_engine_response_body_captured(tmp_path):
    manager = await _launched_capturing_manager(tmp_path, capture_body=True)
    req = Request(object(), body=b"q=1")
    manager._on_request(req)
    entry = manager._network_requests[0]
    resp = Response(req, body=b"\x00\xffbinary-resp")
    await manager._fetch_response_body(resp, entry)
    assert base64.b64decode(entry["engine_response_body"]) == b"\x00\xffbinary-resp"
    assert entry["engine_response_body_size"] == 13
    assert entry["engine_response_body_truncated"] is False
    assert manager._engine_body_stats["response_captured"] == 1
    assert manager._engine_body_stats["response_unavailable"] == 0


@pytest.mark.asyncio
async def test_engine_response_body_unavailable_is_counted(tmp_path):
    manager = await _launched_capturing_manager(tmp_path, capture_body=True)
    req = Request(object())
    manager._on_request(req)
    entry = manager._network_requests[0]
    await manager._fetch_response_body(Response(req, fail=True), entry)
    assert entry["engine_response_body"] is None
    assert entry["response_body"] is None
    assert manager._engine_body_stats["response_unavailable"] == 1


@pytest.mark.asyncio
async def test_tools_expose_engine_body_fields(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path, capture_body=True)
    req = Request(object(), body=b"q=1")
    manager._on_request(req)
    await manager._fetch_response_body(Response(req), manager._network_requests[0])
    manager._on_request(Request(object(), body=None))  # GET，无引擎 body
    monkeypatch.setattr(network, "browser_manager", manager)

    summaries = await network.list_network_requests()
    with_body = next(s for s in summaries if s["has_engine_request_body"])
    assert with_body["has_engine_response_body"] is True
    without_body = next(s for s in summaries if not s["has_engine_request_body"])
    assert without_body["has_engine_response_body"] is False

    # 默认不含 base64 全量，只报可用性；include_body=True 才带全量
    detail = await network.get_network_request(with_body["id"])
    assert detail["engine_request_body_available"] is True
    assert detail["engine_response_body_available"] is True
    assert "engine_request_body" not in detail
    full = await network.get_network_request(with_body["id"], include_body=True)
    assert base64.b64decode(full["engine_request_body"]) == b"q=1"
    assert base64.b64decode(full["engine_response_body"]) == b'{"ok":true}'


@pytest.mark.asyncio
async def test_capture_status_reports_engine_body_stats(monkeypatch, tmp_path):
    manager = await _launched_capturing_manager(tmp_path, capture_body=True)
    req = Request(object(), body=b"q=1")
    manager._on_request(req)
    await manager._fetch_response_body(Response(req), manager._network_requests[0])
    monkeypatch.setattr(network, "browser_manager", manager)
    result = await network.network_capture("status")
    assert result["engine_body_stats"] == {
        "request_captured": 1, "response_captured": 1, "response_unavailable": 0}
