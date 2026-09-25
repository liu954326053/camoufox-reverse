"""第十二阶段：vm_loop route fulfill 链路保真性测试。

背景：phase12 leboncoin.fr（DataDome）插桩会话出现 16 条
auth.leboncoin.fr/_next/static/chunks/*.js 404。离线证据显示 404 为上游
源站真实响应（CloudFront→envoy→prod-authsecu，9 字节 "Not Found" 体），
本测试组固化 route_handler 的 fulfill 语义，证明 route 层：
1. 不制造、不放大上游错误状态（404 原样透传）；
2. 不改写响应时 body 逐字节保真；
3. fulfill 头绝不残留 content-encoding / content-length /
   transfer-encoding（Playwright route.fetch 已解码，残留会让浏览器
   二次解码并静默丢弃响应）；
4. 解析失败（esprima 不认的现代语法）回退原字节，不破坏页面。
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from camoufox_reverse_mcp.tools.vm_loop import vm_loop_trace, _LOOP_ROUTES


# ---------------- fake route/context 装置 ----------------

class FakeRequest:
    def __init__(self, url, resource_type="script", headers=None):
        self.url = url
        self.resource_type = resource_type
        self.headers = headers or {}
        self.method = "GET"
        self.post_data = None


class FakeResponse:
    """模拟 Playwright route.fetch() 的返回值：body() 是解码后的明文。"""

    def __init__(self, status, headers, body: bytes):
        self.status = status
        self.headers = headers
        self._body = body

    async def body(self):
        return self._body


class FakeRoute:
    def __init__(self, url, status, headers, body: bytes,
                 resource_type="script", req_headers=None):
        self.request = FakeRequest(url, resource_type, req_headers)
        self._response = FakeResponse(status, headers, body)
        self.fulfilled = None
        self.continued = False

    async def fetch(self, **kwargs):
        # 与 Playwright route.fetch 对齐：接收 max_redirects 等可选参数
        self.fetch_kwargs = kwargs
        return self._response

    async def fulfill(self, status=None, headers=None, body=None):
        self.fulfilled = {"status": status, "headers": dict(headers or {}),
                          "body": body}

    async def continue_(self):
        self.continued = True


class FakeCtx:
    def __init__(self):
        self.handlers = {}

    async def route(self, pattern, handler):
        self.handlers[pattern] = handler

    async def unroute(self, pattern):
        self.handlers.pop(pattern, None)


async def _install_capture(pattern="**"):
    """安装 route 并返回 (handler, stats_holder)。"""
    ctx = FakeCtx()
    with patch("camoufox_reverse_mcp.tools.vm_loop.browser_manager") as bm:
        bm.contexts = {"default": ctx}
        bm.add_persistent_script = AsyncMock()
        r = await vm_loop_trace(action="install", url_pattern=pattern)
    assert r.get("status") == "tracing", r
    return ctx.handlers[pattern]


async def _stop_stats(pattern="**") -> dict:
    r = await vm_loop_trace(action="stop", url_pattern=pattern)
    assert r["status"] == "stopped" and r["removed"], r
    return r["removed"][0]["stats"]


@pytest.fixture(autouse=True)
def _clean_routes():
    yield
    _LOOP_ROUTES.clear()


# ---------------- 保真性用例 ----------------

@pytest.mark.asyncio
async def test_fulfill_passthrough_404_byte_identical():
    """上游 404（leboncoin auth chunk 实测形态：text/plain + 9B "Not Found"）
    必须原样透传：状态码、body 逐字节不变，route 层不制造 404。"""
    handler = await _install_capture()
    route = FakeRoute(
        "https://auth.leboncoin.fr/_next/static/chunks/061mv098ilz09.js",
        404,
        {"content-type": "text/plain; charset=utf-8",
         "content-length": "9",
         "x-cache": "Error from cloudfront"},
        b"Not Found")
    await handler(route)
    assert route.fulfilled is not None and not route.continued
    assert route.fulfilled["status"] == 404
    assert route.fulfilled["body"] == b"Not Found"
    low = {k.lower() for k in route.fulfilled["headers"]}
    assert "content-length" not in low  # 长度头必须剥离（body 经解码链路）
    stats = await _stop_stats()
    assert stats["route_errors"] == 0


@pytest.mark.asyncio
async def test_fulfill_strips_encoding_headers_on_passthrough():
    """不改写的 JS（无 while(true) 循环）：decoded body 逐字节透传，
    fulfill 头不得残留 content-encoding / content-length /
    transfer-encoding，否则浏览器对明文二次解码会静默丢响应。"""
    handler = await _install_capture()
    body = b"function add(a,b){return a+b;}"
    route = FakeRoute(
        "https://x.example/static/app.js",
        200,
        {"content-type": "application/javascript",
         "content-encoding": "br",
         "content-length": "1234",
         "transfer-encoding": "chunked"},
        body)
    await handler(route)
    f = route.fulfilled
    assert f["status"] == 200
    assert f["body"] == body  # 逐字节保真
    low = {k.lower() for k in f["headers"]}
    assert "content-encoding" not in low
    assert "content-length" not in low
    assert "transfer-encoding" not in low
    stats = await _stop_stats()
    assert stats["route_errors"] == 0


@pytest.mark.asyncio
async def test_fulfill_parse_failure_keeps_original_bytes():
    """esprima 不认的现代语法（?. / ??）→ parse_failure：原字节透传，
    不破坏响应；parse_failures 计数 +1（第十二阶段归因：103/153 即此类）。"""
    handler = await _install_capture()
    body = b"const a = b?.c ?? 1; export default a;"
    route = FakeRoute(
        "https://www.leboncoin.fr/_next/static/chunks/2wr590tzs2izv.js",
        200,
        {"content-type": "application/javascript",
         "content-encoding": "gzip"},
        body)
    await handler(route)
    f = route.fulfilled
    assert f["body"] == body  # 解析失败不破坏原文件
    assert {k.lower() for k in f["headers"]}.isdisjoint(
        {"content-encoding", "content-length", "transfer-encoding"})
    stats = await _stop_stats()
    assert stats["parse_failures"] >= 1
    assert stats["js_rewritten"] == 0
    # 第十二阶段拆分：真实 JS 语法超 esprima 口径 → syntax，
    # 不得误判为 nonscript
    assert stats["parse_failures_syntax"] == stats["parse_failures"]
    assert stats["parse_failures_nonscript"] == 0


@pytest.mark.asyncio
async def test_fulfill_rewritten_js_strips_encoding_and_fixes_content_type():
    """改写的 JS（含 while(true)）：tick 注入生效， fulfill 头无编码残留，
    content-type 显式声明 charset=utf-8（body 重新 utf-8 编码）。"""
    handler = await _install_capture()
    body = b"function vm(p){var s=0;while(true){if(s==2)break;s=p[s];}return s;}"
    route = FakeRoute(
        "https://x.example/sdk.js",
        200,
        {"content-type": "application/javascript",
         "content-encoding": "gzip",
         "content-length": "999"},
        body)
    await handler(route)
    f = route.fulfilled
    assert b"__mcp_vm_loop_tick" in f["body"]
    low = {k.lower() for k in f["headers"]}
    assert "content-encoding" not in low and "content-length" not in low
    assert f["headers"]["content-type"].endswith("charset=utf-8")
    stats = await _stop_stats()
    assert stats["js_rewritten"] == 1 and stats["loops"] == 1


@pytest.mark.asyncio
async def test_fulfill_html_404_passthrough():
    """404 text/html（DataDome/源站错误页）：inline 改写找不到 script 块，
    HTML 逐字节透传，状态码不变。"""
    handler = await _install_capture()
    body = b"<html><body>Not Found</body></html>"
    route = FakeRoute(
        "https://auth.leboncoin.fr/api/authorizer/v2/authorize?x=1",
        404,
        {"content-type": "text/html; charset=utf-8", "content-length": "37"},
        body,
        resource_type="document")
    await handler(route)
    f = route.fulfilled
    assert f["status"] == 404
    assert f["body"] == body.decode("utf-8")  # HTML 路径 fulfill 的是 str
    stats = await _stop_stats()
    assert stats["route_errors"] == 0


@pytest.mark.asyncio
async def test_fulfill_no_double_fetch_semantics():
    """既有语义固化：route.fetch() 之后一律 fulfill，不得 continue_
    （fetch+continue_ 会让令牌挑战页物理双发——第十一阶段修复，防回退）。"""
    handler = await _install_capture()
    route = FakeRoute(
        "https://x.example/api/data", 200,
        {"content-type": "application/json"}, b"{}",
        resource_type="fetch")
    await handler(route)
    assert route.fulfilled is not None and not route.continued  # 非 JS 也不允许 continue_
    await _stop_stats()


# ---------------- 第十二阶段：parse_failures 统计口径拆分 ----------------

@pytest.mark.asyncio
async def test_parse_failure_nonscript_404_not_found():
    """404 的 9 字节 "Not Found" 错误页（URL 后缀 .js、content-type
    text/plain——leboncoin auth chunk 实测形态）被当脚本解析失败：
    归 parse_failures_nonscript，不归 syntax；总数保持兼容。"""
    handler = await _install_capture()
    route = FakeRoute(
        "https://auth.leboncoin.fr/_next/static/chunks/061mv098ilz09.js",
        404,
        {"content-type": "text/plain; charset=utf-8",
         "x-cache": "Error from cloudfront"},
        b"Not Found")
    await handler(route)
    assert route.fulfilled is not None and route.fulfilled["body"] == b"Not Found"
    stats = await _stop_stats()
    assert stats["parse_failures"] == 1
    assert stats["parse_failures_nonscript"] == 1
    assert stats["parse_failures_syntax"] == 0


@pytest.mark.asyncio
async def test_parse_failure_nonscript_tiny_body_even_with_js_content_type():
    """content-type 标称 JS 但 body 极小且无任何 JS 语法特征
    （源站异常回包的错误体）：仍归 nonscript——body 明显不是 JS。"""
    handler = await _install_capture()
    route = FakeRoute(
        "https://x.example/static/chunk.js",
        200,
        {"content-type": "application/javascript"},
        b"Not Found")
    await handler(route)
    assert route.fulfilled["body"] == b"Not Found"
    stats = await _stop_stats()
    assert stats["parse_failures"] == 1
    assert stats["parse_failures_nonscript"] == 1
    assert stats["parse_failures_syntax"] == 0


@pytest.mark.asyncio
async def test_parse_failure_nonscript_html_error_page_on_js_url():
    """.js URL 回 HTML 形态错误页（content-type 非 JS、body 以 "<" 开头、
    长度超 64B）：归 nonscript，且 body 逐字节透传不破坏。"""
    handler = await _install_capture()
    body = (b"<!DOCTYPE html><html><head><title>404 Not Found</title></head>"
            b"<body><h1>Not Found</h1><p>The requested resource was not "
            b"found on this server.</p></body></html>")
    route = FakeRoute(
        "https://auth.leboncoin.fr/_next/static/chunks/deadbeef.js",
        404,
        {"content-type": "text/plain; charset=utf-8"},
        body)
    await handler(route)
    assert route.fulfilled["body"] == body
    stats = await _stop_stats()
    assert stats["parse_failures"] == 1
    assert stats["parse_failures_nonscript"] == 1
    assert stats["parse_failures_syntax"] == 0


@pytest.mark.asyncio
async def test_parse_failure_syntax_modern_js_counts_syntax():
    """真实 JS（content-type JS、含语法特征、体量正常）因 ?. / ?? 等
    超 esprima ES2017 口径解析失败：归 parse_failures_syntax。"""
    handler = await _install_capture()
    body = (b"const cfg = window.__dd?.cfg ?? {}; "
            b"export const tick = () => cfg?.tick ?? 0; "
            b"const pad = 'x'.repeat(80);")
    route = FakeRoute(
        "https://www.leboncoin.fr/_next/static/chunks/framework.js",
        200,
        {"content-type": "application/javascript"},
        body)
    await handler(route)
    assert route.fulfilled["body"] == body  # 解析失败回退原字节
    stats = await _stop_stats()
    assert stats["parse_failures"] == 1
    assert stats["parse_failures_syntax"] == 1
    assert stats["parse_failures_nonscript"] == 0


@pytest.mark.asyncio
async def test_parse_failure_inline_script_counts_syntax():
    """HTML 内联 <script> 的解析失败一律归 syntax——内联脚本内容
    按定义是 JS，不存在「非脚本被当脚本」的形态。"""
    handler = await _install_capture()
    body = (b"<html><head><script>const a = b?.c ?? 1;</script></head>"
            b"<body>ok</body></html>")
    route = FakeRoute(
        "https://x.example/page", 200,
        {"content-type": "text/html; charset=utf-8"}, body,
        resource_type="document")
    await handler(route)
    assert route.fulfilled is not None
    stats = await _stop_stats()
    assert stats["parse_failures"] == 1
    assert stats["parse_failures_syntax"] == 1
    assert stats["parse_failures_nonscript"] == 0
