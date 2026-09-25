# reverse8 phase13：引擎级 fetch/XHR 请求体/响应体零污染拦截 —— 落地记录（2026-09-26）

> 承接 phase12（引擎层 initiator 栈）。结论先行：**主链路打通，但引擎侧零改动**——
> juggler 上游已具备 body 捕获能力（`readRequestPostData` / `ResponseStorage` /
> `getResponseBody`），stock playwright driver 已透出（`postDataBuffer` /
> `response.body()`）。真实缺口全在 MCP 透出层：二进制请求体被 `post_data` 的
> utf-8 解码丢弃、响应体只有有损文本、无尺寸/截断自描述。本阶段补齐 MCP 字段契约。

## 1. 与任务书预设的偏差（如实说明）

任务书按 phase12 链路预设「新增 collector + 改 NetworkObserver/Protocol + driver 补丁 +
omni.ja 重打包」。逐环核查后：

| 环节 | 核查结果 |
|---|---|
| 引擎采集 | **已存在**。`additions/juggler/NetworkObserver.js` 的 `readRequestPostData`（`http-on-modify-request` 时读 `nsIUploadChannel`，≤10MB，base64）与 `ResponseStorage`（父进程 `onStopRequest` 落盘，100MB/标签、10MB/条上限）均为上游既有，页面世界零接触 |
| 协议字段 | **已存在**。`Protocol.js` `requestWillBeSent.postData`、`Network.getResponseBody` |
| driver 透出 | **已存在**。stock ffNetworkManager 把 `postData` 解码为 `postDataBuffer` 进 Request initializer（phase12 补丁锚点里那行 `postDataBuffer` 就是它）；`resp.body()` 走 juggler `getResponseBody`。无需新增 driver 补丁 |
| omni.ja | **未重打包**。引擎与协议零改动，reverse.5 / reverse.9 在用构建已含全部能力（本次实测即用未改动的 reverse.9 跑通），重打包只会产生无意义重写。故未创建 `/tmp/omni.ja.bak-reverse{5,9}-prebodycap`；phase12 的 `-preinitiator` 备份仍是在用构建的唯一改动基线 |
| MCP 透出层 | **真实缺口**，本阶段补齐 |

若后续要放宽引擎侧上限（请求体 10MB 静默缺席、响应 10MB/条 evict），那时才需要动
omni.ja，届时按 phase12 的重打包流程走并先备份。

## 2. 改动文件清单

全部在 `integrations/camoufox-reverse-mcp/`，引擎/additions 零改动：

| 文件 | 改动 |
|---|---|
| `src/camoufox_reverse_mcp/browser.py` | `_on_request` 新增 `engine_request_body`（base64 无损，来源 `req.post_data_buffer`）+ `engine_request_body_size` + `engine_request_body_truncated`；`_fetch_response_body` 新增 `engine_response_body`（原始字节 base64）+ size/truncated；新增 `_engine_body_stats = {request_captured, response_captured, response_unavailable}` 计数自描述。截断上限沿用 `MAX_BODY_SIZE=200_000` |
| `src/camoufox_reverse_mcp/tools/network.py` | `list_network_requests` 摘要加 `has_engine_request_body` / `has_engine_response_body`；`get_network_request` 默认只报 `engine_*_available`（base64 全量需 `include_body=True`）；`network_capture(status)` 带 `engine_body_stats` |
| `tests/test_engine_body_capture.py` | 新增 8 个离线测试：base64 无损、二进制请求体在 `post_data` 解码失败时仍存活、无请求体记 None、截断自描述、响应体捕获/不可得计数、工具层输出形态、status 统计 |

**字段语义**（命名对齐 `engine_initiator_stack` 风格）：
- `engine_request_body`：base64 文本；`None` = 无请求体（GET 等）或引擎层缺席（>10MB）。
- `engine_response_body`：base64 文本；仅 `network_capture(capture_body=True)` 时填充；
  `None` = 不可得（redirect 响应、SW 合成、evicted）。
- 与既有字段的关系：`request_post_data`（文本，二进制丢）与 `request_body_base64`
  （evidence 内部字段，无截断标记）维持原样不互覆；`engine_request_body` 是带
  尺寸/截断自描述的对外契约字段。`response_body`（文本，latin-1 兜底有损）同样保留。

## 3. 实测证据（本地夹具，零外网；reverse.9 构建，MCP BrowserManager 真实链路）

夹具 `artifacts/phase13-bodycap/index.html`：fetch 文本 POST、XHR **二进制** POST
（`Uint8Array [0,1,255,254,65,66]`）、二进制响应端点 `/api/bin`、普通 GET。
脚本 `artifacts/phase13-bodycap/run_mcp_live.py`，本地 `http.server` @127.0.0.1:8909。

**body 捕获（内存条目，base64 解码后核验）**：

```
fetch POST  req={"hello":"world","n":42} (24B)   resp={"ok":true}
xhr   POST  req=\x00\x01\xff\xfeAB (6B, 非utf-8)  resp={"ok":true}
fetch GET /api/bin  req=None                    resp=\x00\xff\x01\xfeHi (6B, 非utf-8)
fetch GET   req=None                            resp={"ok":true}
STATS {request_captured: 2, response_captured: 5, response_unavailable: 0}
```

关键场景——XHR 二进制请求体在旧链路 `request_post_data` 必然记 `None`
（utf-8 解码失败），`engine_request_body` 无损存活。

**落盘核验**：`persist_before_navigation` 后 evidence 文件（run 目录
`raw/mcp-network/requests/<id>/<rev>.json`）中 9 份含引擎 body 字段，
`b'\x00\x01\xff\xfeAB'` / `b'\x00\xff\x01\xfeHi'`
base64 往返一致。路径 `/tmp/mcp-engine-bodycap-test/runs/a588ba77497a4743b68d0ed1b3c20306/raw/mcp-network/requests/`。

**零污染裁决**（夹具页页面世界探针，打补丁 MCP 链路实测）：

```json
{"fetch_native": true, "xhr_open_native": true, "xhr_send_native": true,
 "tostring_native": true, "tostring_on_fetch": true,
 "suspicious_globals": ["DisposableStack", "AsyncDisposableStack"]}
```

与 phase12 对照组（未改动 reverse.8）逐字节一致：`DisposableStack`/
`AsyncDisposableStack` 为 Firefox 152 标准内置。采集链路为引擎层只读
（`nsIUploadChannel` clone/seek 读、父进程 stream listener 旁路拷贝），
不替换/不包装/不读取任何页面 JS 对象——`fetch`、`XHR.open/send`、
`Function.prototype.toString` 全为 native。

## 4. pytest

`PYTHONPATH=src python3.12 -m pytest tests -q`：**160 passed, 2 skipped**
（phase12 基线 152 + 新增 8，零回归）。

## 5. 已知限制

- **请求体 >10MB**：juggler `readRequestPostData` 上限，超限引擎层即缺席，
  `engine_request_body=None` 且无显式标记（引擎未自描述，改它才需要动 omni.ja）。
- **MCP 层 200KB 截断**：`MAX_BODY_SIZE`，截断时 `engine_*_truncated=True` +
  `engine_*_size` 报全量字节数，自描述。
- **SSE / streaming / 长连接响应**：juggler `ResponseStorage` 在 `onStopRequest`
  才落盘，流未结束时 `resp.body()` 拿不到（playwright 会等 requestFinished）；
  不覆盖流式中途快照。
- **Service Worker 合成响应、redirect 中间响应、存储超上限 evicted**：
  `engine_response_body=None` 并计入 `response_unavailable`，不伪造。
- **响应体捕获需 `network_capture(capture_body=True)`**（沿用既有开关，省内存默认关）；
  请求体捕获无条件。
- evidence 的 `request_body_base64`（无截断标记）与 `engine_request_body` 并存，
  前者是内部关联键，后者是对外契约；读 body 一律用后者。
