# 第十二阶段补：被动模式 initiator JS 栈——引擎层可行性评估与最小原型

日期：2026-09-26 ｜ 状态：**已落地**（可行性确认 + 原型 + 全链路实现与实测通过） ｜ 前置：`docs/reverse8-phase12-gsxt-passive-scorecard-2026-09-26.md`（52/52 initiator 全 null 的缺口坐实）

> 落地记录见第 6 节；第 4 节路线图各项均已完成，状态标注在各项末尾。

## 0. 结论（一句话）

**可行。** Firefox 在 content 进程的 `HttpChannelChild::AsyncOpen` 里同步广播 `http-on-opening-request` 观察者通知，此刻发起请求的页面 JS 仍在调用栈上，特权（chrome）代码读 `Components.stack` 即可拿到完整 initiator 栈——这正是 DevTools 网络面板「发起者」列的机制，全程不触碰页面世界任何对象，满足加速乐类零容忍目标的硬约束；最小原型已在本地夹具页上抓到 fetch/XHR 的归因栈（函数名 + 行号），零污染探针干净。

## 1. 现状归因：MCP 层 initiator 为什么是 null

`initiator_stack` 当前**唯一**来源是页面世界 hook，与引擎无关：

- `integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/network_evidence.py:127` `NetworkEvidence.initiator()`：把 Playwright 的 request 事件按 `(frame, document, url, method, body)` 签名与 `self.calls` 里的 hook 调用记录关联，唯一匹配才回填 `call.get("stack")`；否则 `initiator_stack=None` 且诚实自描述 `correlation=unmatched/ambiguous`。
- `self.calls` 只由 `hooks/network_raw.js`（页面世界 XHR/fetch 包装，抓 `Error().stack`）经 `__mcp_network_evidence` binding 上送（`network_evidence.py:97-105` 的 `install()` 注册 init script + binding）。
- `tools/network.py:223` `get_request_initiator()` 只是读这份关联结果。

因此被动/零变异模式（不装 hook）下 `calls` 为空，52/52 全 null 是**结构性必然**，不是采集 bug。Juggler 引擎层的 `NetworkObserver` 事件本身从不携带 JS 栈（见下节），MCP 无源可取。

## 2. 可行通道调研

### 2.1 Juggler 现有网络事件里有什么

`additions/juggler/NetworkObserver.js`（父进程 chrome 模块）：

- `_sendOnRequest`（L480-509）目前发出：url、frameId、requestId、redirectedFrom、postData、headers、method、navigationId、`cause`/`internalCause`（来自 `loadInfo.externalContentPolicyType` / `internalContentPolicyType`，即 `TYPE_FETCH`/`TYPE_XMLHTTPREQUEST` 等——**资源类型**有，**JS 栈没有**）。
- `NetworkRequest` 构造（L108-114）已读 `loadInfo`（frameBrowsingContext / browsingContext），`triggeringPrincipal`、`loadingDocument` 同样可得。
- 但 `http-on-modify-request` 等观察者跑在**父进程**，`Components.stack` 在父进程只反映父进程栈，页面 JS 帧不在场——父进程层结构性拿不到 initiator 栈。

### 2.2 Firefox 源码里的现成机制（DevTools 同款）

源码树 `camoufox-152.0.4-beta.30/` 内三条关键证据：

1. **同步通知点**：`netwerk/protocol/http/HttpChannelChild.cpp:2370` 注释原文——"We notify "http-on-opening-request" observers in the child process so that devtools can capture a stack trace at the appropriate spot." 通知在 `AsyncOpen` 内同步发出，页面 fetch/XHR 的 JS 帧仍在栈上。
2. **栈采集实现**：`devtools/server/actors/resources/network-events-stacktraces.js`（`NetworkEventStackTracesWatcher`）——content 进程注册 `http-on-opening-request` / `document-on-opening-request` / `network-monitor-alternate-stack` 观察者 + ChannelEventSink（跟踪重定向后 channelId 换绑），`observe()` 里 `let frame = Components.stack; frame.caller` 起逐帧取 `filename/lineNumber/columnNumber/functionName/asyncCause`（L141-154），并用 `frame.caller || frame.asyncCaller` 穿异步边界；最后 `NetworkUtils.removeChromeFrames` 滤掉 `resource://`/`chrome://` 帧。
3. **跨进程关联键**：`channelId` 父子进程一致——子进程生成 `mChannelId` 并经 `openArgs.channelId() = mChannelId`（`HttpChannelChild.cpp:2561`）发给父进程，父进程 `HttpChannelParent.cpp:520` `SetChannelId(aChannelId)` 采纳。Juggler 父进程侧 `requestId = httpChannel.channelId + ''`（NetworkObserver.js L113），因此 content 进程抓到的栈可以按 channelId 精确并入现有 Juggler 请求事件，无需任何 URL/时序猜测式关联。

### 2.3 Juggler 的 content 进程落脚点

`additions/juggler/content/main.js` 由 `JugglerFrameChild.sys.mjs`（JSWindowActorChild）在每个 content 进程 import 一次，是特权代码、与页面世界隔离——天然就是挂这个观察者的地方（原型即在此验证）。

## 3. 最小原型验证（已跑通）

### 做法

- 直接补丁已安装 bundle 的 omni.ja 中 `chrome/juggler/content/content/main.js`（与 `additions/juggler/content/main.js` 逐字节一致，已 diff 确认），追加一个 content 进程 `http-on-opening-request` 观察者：仿 DevTools 走 `Components.stack`，记录 `{channelId, pid, url, method, stack}`。**验证后已恢复原 omni.ja**（备份在 `/tmp/omni.ja.bak-reverse5`，已回写并核对 0 处残留）。
- 夹具：`/tmp/initiator-fixture/index.html`（本地 `python http.server`，嵌套命名函数 `levelTwoFetch→levelOneFetch` 发 POST fetch、`xhrWrapper→xhrSender` 发 POST XHR）。
- 启动：`Camoufox(persistent_context=True, user_data_dir=/tmp/initiator-proto-profile, headless=True)` + `firefox_user_prefs={'browser.dom.window.dump.enabled': True}`，`DEBUG=pw:browser` 抓取 content 进程 dump 输出。

### 结果

抓到的 fetch 发起栈（逐字）：

```
levelOneFetch   http://127.0.0.1:8907/  L7  C10
levelTwoFetch   http://127.0.0.1:8907/  L9  C35
window.__done<  http://127.0.0.1:8907/  L20 C9
(顶层脚本)       http://127.0.0.1:8907/  L23 C3
```

XHR 发起栈：`xhrSender/<`(Promise executor)→`xhrSender` L11→`xhrWrapper` L18→`window.__done<` L21。函数名、行列号与夹具源完全一致；channelId（如 `12884901895`）随记录产出，可直接用于与父进程事件合并。同进程 chrome 自发的 favicon 请求栈帧全部是 `resource://`——验证了「无页面帧即不可归因、chrome 帧可滤除」的判据。

### 零污染性验证（硬约束）

- 观察者注册在 chrome 特权域，**不替换、不包装、不读取**任何页面 JS 对象；页面探针实测：`fetch.toString()`、`XHR.prototype.open/send.toString()` 均含 `[native code]`，`Object.getOwnPropertyNames(window)` 无可疑新增全局（仅 Firefox 152 标准内置 `DisposableStack`/`AsyncDisposableStack`）。
- 对 channel 只读（QueryInterface + 读 URI/method/channelId），不改请求头/时序/负载；`Services.obs` 观察者列表页面世界不可枚举。
- 性能：每请求一次 ≤32 帧的栈遍历，与 DevTools 常态化开销同级。

### 原型中的边界情况（不影响结论）

- content 进程沙箱禁止文件写（`ProfD` dirsvc 在子进程抛 `NS_ERROR_FAILURE`，`TmpD` 写抛 `NS_ERROR_FILE_ACCESS_DENIED`）——原型改走 `dump()`+stdout 取证。**正式实现的输出通道必须走 IPC**（见第 4 节），不能依赖文件。

## 4. 落地建议（已全部实施，见第 6 节）

1. **content 进程抓栈器**（小）：在 `additions/juggler/content/` 新增模块（或挂进 `main.js`），移植 DevTools `NetworkEventStackTracesWatcher` 逻辑（三个 topic + ChannelEventSink 重定向换绑 + `removeChromeFrames`），按 channelId 存栈 Map，LRU 上限。——**已落地**：`additions/juggler/content/InitiatorStackCollector.js`。
2. **content→parent 上送**（小）：复用 juggler `SimpleChannel` 或新增 actor message，把 `{channelId, stack}` 发父进程；父进程 `NetworkObserver.js` 的 `NetworkRequest` 构造时按 channelId 取出并入 `PageNetwork.Events.Request` 负载（如 `initiatorStack` 字段）。注意清理时序：栈在 `http-on-opening-request`（先于父进程 `http-on-modify-request`）时已就绪，时序天然成立。——**已落地**：实际选用 `Services.cpmm/ppmm` 消息（`juggler:initiator-stack`），比 SimpleChannel 更少握手时序依赖；实测父进程合并 100% 命中。
3. **协议/driver 透传**（中，需验证）：Juggler 协议事件 schema（`additions/juggler/protocol/`）加字段后，playwright driver 的 juggler client 是否透传未知事件参数需实测；不透传则需同步补丁 driver（camoufox pythonlib 内置 playwright）或让 MCP 走独立通道读栈。——**已落地，实测结论：不透传，且需补三处**。driver 把 juggler 事件字段显式拷贝进 `Request` initializer（`ffNetworkManager`），且 `DispatcherConnection.sendCreate` 会用 `protocol/validator.js` 的 `RequestInitializer` scheme **剥掉未声明键**。因此补丁覆盖三处：`ffNetworkManager` 记字段 → `networkDispatchers.RequestDispatcher` initializer 加字段 → validator scheme 声明字段；分拆/捆绑（coreBundle.js）两种 driver 布局各有锚点，封装为 `_playwright_initiator_patch.py`（仿既有 `_playwright_patch.py` 的幂等启动补丁模式，MCP 启动时自动应用）。
4. **MCP 层接入**（小）：`network.py`/`network_evidence.py` 优先取引擎层 `initiatorStack`；hook 关联降级为交叉校验。被动模式自此可出 `correlation=exact` 的 initiator。——**已落地**，合并规则按任务要求定为**双通道并存、互不覆盖**：`engine_initiator_stack`（引擎栈，零污染，零容忍目标唯一通道）独立于 hook 通道的 `initiator_stack`；`browser.py` 按请求记账（`captured`/`unavailable` 计数自描述），`list_network_requests` 摘要带顶帧、`get_network_request`/`get_request_initiator` 带全量帧。
5. **覆盖度备注**：DevTools 同款边界照单继承——同步发起（含事件循环任务内的 fetch/XHR）全有栈；真正异步 open 的 channel 栈为空（DevTools 用 `network-monitor-alternate-stack` 补救，可后补）；WebSocket 走 `nsIWebSocketChannel.serial` 键、Worker 走 `nsIWorkerChannelInfo`——两者源码里均有现成处理（`network-events-stacktraces.js` L91-114），二期可补。——**维持为后续工作**；当前实现对拿不到栈的情形如实记 `null` 并计数（实测：导航 document 请求 `unavailable`，见第 6 节）。

## 6. 落地记录（2026-09-26）

### 6.1 实现清单（文件级）

**浏览器引擎层（`additions/`，源码真相）**

| 文件 | 改动 |
|---|---|
| `additions/juggler/content/InitiatorStackCollector.js` | 新增。content 进程 chrome 特权观察者：`http-on-opening-request` / `document-on-opening-request` 同步抓 `Components.stack`（`caller \|\| asyncCaller` 穿透、≤32 帧、`resource://`/`chrome://` 帧过滤、按顶层 browsingContext 归属过滤），`Services.cpmm.sendAsyncMessage('juggler:initiator-stack', {channelId, stack})` 上送父进程 |
| `additions/juggler/content/main.js` | `initialize()` 里为每个顶层 tab 装配 collector |
| `additions/juggler/content/JugglerFrameChild.sys.mjs` | `didDestroy` 里 dispose collector |
| `additions/juggler/NetworkObserver.js` | 父进程：ppmm listener 收栈（Map≤1000 FIFO），`_onRedirect` 跨重定向传递，`NetworkRequest._sendOnRequest` 按 channelId 并入 `initiatorStack` 字段，请求终态时清理 |
| `additions/juggler/protocol/Protocol.js` | `networkTypes.InitiatorStackFrame` 类型 + `requestWillBeSent.initiatorStack` 可选字段（必需：juggler `checkScheme` 拒绝未声明键） |
| `additions/juggler/jar.mn` | 打包清单登记新文件 |

**当前在用浏览器构建（omni.ja 补丁路径，沿用原型方式）**：上述 5 个文件已打入 `~/Library/Caches/camoufox/browsers/whitenightshadow/152.0.4-beta.30-reverse.5/`（pythonlib active_version）与 `.../reverse.9/`（reverse runtime `BROWSER_SELECTOR` 钉的版本，MCP 实际使用）两处 omni.ja；原文件备份在 `/tmp/omni.ja.bak-reverse{5,9}-preinitiator`。已在运行的浏览器进程不受影响，下次 launch 生效。

**driver / Python 透出层**

| 文件 | 改动 |
|---|---|
| `integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/_playwright_initiator_patch.py` | 新增。启动时幂等补丁 playwright driver 三处（ffNetworkManager 记 `_engineInitiatorStack` → RequestDispatcher initializer 加 `engineInitiatorStack` → validator `RequestInitializer` 声明字段），split 与 coreBundle 双布局锚点，锚点不唯一即拒写 |
| `integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/__main__.py` | 启动时调用补丁 |

已对两套环境生效：framework Python 3.12 的 playwright（split 布局，pythonlib 直连路径）与 MCP venv 的 playwright 1.60（coreBundle 布局，codex MCP 路径）。

**MCP 层**

| 文件 | 改动 |
|---|---|
| `src/camoufox_reverse_mcp/browser.py` | `_on_request` 从 `req._impl_obj._initializer["engineInitiatorStack"]` 读栈写入条目 `engine_initiator_stack`；`_engine_initiator_stats = {captured, unavailable}` 计数自描述 |
| `src/camoufox_reverse_mcp/tools/network.py` | `list_network_requests` 摘要加 `has_engine_initiator_stack` + `engine_initiator_top`；`get_network_request` 经 `dict(r)` 自动带全量帧；`get_request_initiator` 输出 `engine_initiator_stack` 与统计；`network_capture(status)` 带统计 |
| `tests/test_engine_initiator_stack.py` | 新增 10 个离线测试（字段读取、null 计数、畸形输入、三个工具的输出形态、双通道并存、补丁幂等/拒歧义/双布局） |

**合并规则**：引擎栈与 hook 栈**并存不互覆**。`initiator_stack`（页面 hook 通道，需装 hook、有污染、异步上下文更丰富）维持原逻辑；`engine_initiator_stack`（引擎通道，零污染、零容忍目标唯一可用通道）独立字段。`get_request_initiator` 两者都报，由调用方按目标容忍度选用。

### 6.2 实测证据（本地夹具，零外网）

夹具 `/tmp/initiator-fixture/index.html`：嵌套命名函数发 POST fetch（`levelTwoFetch→levelOneFetch`）、POST XHR（`xhrWrapper→xhrSender`）、DOM 注入 `<script src=/static/lib.js>`（`scriptLoader`）。两条链路实测：

**pythonlib 直连（reverse.5 / reverse.9 均验证）**——`engineInitiatorStack` 抵达 Python initializer：

```
fetch  4帧: levelOneFetch @ /:6 → levelTwoFetch @ /:8 → window.__done< @ /:27 → (顶层) @ /:31
xhr    4帧: xhrSender/< @ /:14 → xhrSender @ /:10 → xhrWrapper @ /:17 → window.__done< @ /:28
script 3帧: scriptLoader/< @ /:23 → scriptLoader @ /:19 → window.__done< @ /:29
document(导航) null —— 诚实记不可用
```

**MCP BrowserManager 真实链路（reverse.9）**：4 条目入账，`STATS {captured: 3, unavailable: 1}`；`list_network_requests` 摘要带顶帧；`get_request_initiator` 返回 `engine_frames=4` 且 hook 通道 `initiator_stack=null / correlation=unmatched`（未装 hook）——双通道并存语义实测成立。日志：`/tmp/live6.log`、`/tmp/live-r9.log`、`/tmp/live-r8-baseline.log`（对照组）、MCP 链路输出见 `/tmp/mcp-engine-stack-test` 会话产物。

**零污染证明**（夹具页页面世界探针，打补丁构建 vs 未改动的 reverse.8 对照组结果逐字节一致）：

```json
{"fetch_native": true, "xhr_open_native": true, "xhr_send_native": true,
 "tostring_native": true, "tostring_on_fetch": true,
 "suspicious_globals": ["DisposableStack", "AsyncDisposableStack"],
 "has_pw_guard_only": ["__pwInitScripts"]}
```

`DisposableStack`/`AsyncDisposableStack` 为 Firefox 152 标准内置；`__pwInitScripts` 是 playwright init-script 守卫，对照组同样存在。采集路径不替换/不包装/不读取任何页面 JS 对象，channel 只读。

**拿不到栈的情形**：导航/浏览器自身发起的请求 `engine_initiator_stack=null` 并计入 `unavailable`（实测 document 导航）；Worker 内发起的请求主栈不可得（DevTools 同边界，alternate-stack 机制二期可补）；driver 未打补丁时全字段为 `null`，由统计与字段缺失自描述。

### 6.3 pytest

`PYTHONPATH=src python3.12 -m pytest tests -q`：**152 passed, 2 skipped**（基线 142 passed + 新增 10，零回归）。

### 6.4 已知限制

- content→parent 走独立 ppmm 消息，与 necko PHttpChannel 在同一 IPC 链路上保序，实测合并 100% 命中；理论竞态（极端进程调度下消息晚于 `http-on-modify-request`）表现为该请求记 `null`，不会错配。
- 栈帧 `filename` 为页面 URL + 行列号（无 sourcemap 还原）。
- omni.ja 为原地补丁；从源码完整重建（`scripts/build-reverse-browser.sh`）时会经 `jar.mn` 自动带上新文件，无需额外步骤。

## 5. 复现物

- 夹具与验证脚本：`/tmp/initiator-fixture/index.html`、`/tmp/initiator-fixture/run_proto.py`
- 原型运行日志（含完整 RECORD 行）：`/tmp/proto-run4.log`
- omni.ja 备份：`/tmp/omni.ja.bak-reverse5`、`/tmp/omni.ja.bak-phase12`（两处 bundle 均已恢复原状）
