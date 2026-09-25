# 第十二阶段：缺口闭合（swarm 并行三轮）

日期：2026-09-26 ｜ 状态：完成 ｜ 方式：swarm 并行（三轮共 7 个子任务）

## 0. 一句话结论

第十一阶段结束时的三块剩余缺口（零容忍目标隐形性、语义钉死深度、厂商覆盖面）
本轮全部推进到落点：**能补的已补上并测试全绿**（wasm 构造器、charCodeAt 中间态、
返回值锚定、在途请求终态、引擎层 initiator 栈、divergence 口径）；
**不能补的已用实测钉死归因并登记边界**（gsxt JS 层不可隐形、DataDome 插桩 404
为平台层 Priority 头限制）。

## 1. 第一轮：能力增强 + 目标实测（3 线并行）

### 1.1 插桩增强批次（vm_loop_trace.js，34 个新契约用例）
- `new WebAssembly.Instance()` 构造器插桩：字节回找去重（bytes_ref）、冻结
  exports 遮蔽计数、来路不明 module 如实记 bytes_unavailable，不伪造。
- charCodeAt/at/codePointAt 逐字符读取合并事件（同串同向连续读取合并、
  64 码元 flush、drain 残余落账）；两处关键修复：hashOf 自污染（用 hook 前
  捕获的原生引用）、包装函数函数级 strict（保 null receiver 的 TypeError 语义）。
- `__mcp_vm_rec_ret(tag, fn, thisArg, args)`：返回值一行锚定，异常原样抛出
  并记 threw，与 rec 共享序号序列与 5000 条环形上限。

### 1.2 P1 厂商实测（详见 reverse8-phase12-p1-probes-2026-09-26.md）
- Cloudflare（turnstile.zeroclover.io）：被动 8s 通过；divergence=diverged。
- DataDome（leboncoin.fr）：被动 203 请求全渲染，5145B 传感器加密 payload
  全文捕获；divergence=diverged。
- Akamai（zappos.com）：被动全渲染；divergence=minor（插桩透明）。
- 途中校正：walmart 实为 PerimeterX、nike 实为 Kasada（x-kpsdk-v）。

### 1.3 gsxt 被动评分（详见 reverse8-phase12-gsxt-passive-scorecard-2026-09-26.md）
十条清单 3✅/7❌：redirect 链字节完整、事件零丢失、无 Worker/wasm 使用；
❌ 集中在被动形态天然边界（VM 内部轨迹）与 initiator 栈 52/52 全 null——
后者回流为引擎层能力（第三轮落地）。挑战 JS（1.29MB ctct_bundle 等）
全文完整，足以离线静态分析。

## 2. 第二轮：实测逼出的缺口修复（4 线并行）

### 2.1 在途/中止请求终态自描述（实锤缺口，已修）
BrowserManager 新增 requestfailed 监听与 finalize_pending_requests：
terminal_state（pending/answered/failed）+ failure_reason + pending_at_close，
network_capture stop 与 close 时统一收口；拿不到原因如实记 unknown。
+7 离线测试。

### 2.2 DataDome 插桩侧 16×404 定案（非我们制造）
三层证据：404 响应带 `x-cache: Error from cloudfront` 与 envoy 头（真实到达
源站）；触发链是插桩会话 authorize 端点拿到陈旧预渲染 HTML（逐字引用死
chunk）；curl 经同代理直取 chunk 跨天稳定 404。残余差异：route.fetch 重发
请求缺 `Priority` 头（Juggler 层丢弃，route 层补不齐）——平台层限制登记。
附产物：fulfill 语义固化测试 6 个 + 两支探针脚本。
parse_failures=103/153 归因：87 条 syntax（esprima ES2017 口径不认识现代
语法，原字节透传）+ 16 条 nonscript（404 错误页），口径已拆分。

### 2.3 divergence 工具口径升级（schema 2）
path 的 ≥16 位 hex/UUID 段归一为 `<token>`、query 只留键名多重集、加密参数
扫 POST body（url_hits/body_hits/no_body 分列）；raw 与 normalized 双口径
对照输出。24 个纯函数测试全过。复验：zappos 维持 minor；leboncoin 维持
diverged（84 条真实差异来自陈旧预渲染连锁 404，非 token 噪音）。

### 2.4 引擎层 initiator 栈（本轮最大件）
机制：content 进程 `http-on-opening-request` 在 AsyncOpen 内同步广播，页面
JS 仍在栈上，chrome 特权观察者抓 `Components.stack`（DevTools 网络面板同款），
ppmm 送父进程按 channelId 并入 Juggler 请求事件；driver 层三处补丁透出
（ffNetworkManager / RequestDispatcher / validator scheme）。**零页面世界
污染实测对照逐字节一致**——零容忍目标（加速乐类）的唯一调用点证据通道。
实测 fetch/XHR/script 三条栈到 Python（行列号与源一致），导航请求诚实记 null。
文档：reverse8-phase12-initiator-stack-feasibility-2026-09-26.md（状态：已落地）。

## 3. 验证总表

| 套件 | 结果 |
|---|---|
| node 契约（verify_vm_loop_inject） | 157 项 ALL PASS |
| 集成 pytest（integrations/camoufox-reverse-mcp） | 152 passed, 2 skipped |
| pythonlib pytest | 363 passed, 5 skipped |
| divergence 脚本测试（scripts/tests） | 24 passed |
| 浏览器实测 | 引擎栈夹具三条栈全通；gsxt/leboncoin/zappos/turnstile 被动全过 |

## 4. 边界登记（诚实口径）

- gsxt（加速乐）：JS 层任何函数级替换不可隐形（第十一阶段定案），被动
  profile + 引擎层 initiator 栈是当前完整形态；引擎级 fetch/XHR 体拦截列为
  观察项。
- DataDome 插桩：route 重发缺 Priority 头（Juggler 丢弃）触发上游陈旧预渲染
  连锁 404——平台层限制，目标功能不受影响（两侧终点一致）。
- charCodeAt 合并：多链交错互相截断、at() 负索引倒读不合并（注释在案）。
- wasm：WebAssembly.Module 构造器未插桩，来路不明 module 记 bytes_unavailable。
- ServiceWorker realm 引擎不可达（第十阶段登记的边界不变）。

## 5. 子文档索引

- [P1 厂商实测](reverse8-phase12-p1-probes-2026-09-26.md)
- [gsxt 被动评分 + 零变异使用指引](reverse8-phase12-gsxt-passive-scorecard-2026-09-26.md)
- [initiator 栈可行性与落地记录](reverse8-phase12-initiator-stack-feasibility-2026-09-26.md)
