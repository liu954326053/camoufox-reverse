# 第十二阶段：gsxt（加速乐）被动产物完备性评分 + 零变异目标使用指引

日期：2026-09-26 ｜ 状态：完成 ｜ 目标：https://www.gsxt.gov.cn/index.html（加速乐，页面世界零容忍目标）

## 0. 会话与产物

- 形态：**被动/零变异**——`capture_profile="raw"`，不装任何 hook，不开 vm_loop，
  `network_capture(action="start", capture_body=True)`，MCP server 显式 `--proxy ""`（内网直连）。
- 结果：挑战通过。标题于墙钟 t=14.2s 变为「国家企业信用信息公示系统」
  （含 MCP 调用开销；网络层 521 首发 → 200 落地跨度 9517ms，与第十一阶段基线 ~9s 一致），
  通过后再等 10s 让遥测跑完。
- 产物根：`/Users/magic/Workspace/camoufox-reverse/artifacts/analysis/phase12/gsxt-passive/`
  - 会话目录：`runs/0e048da4c2334fbd842351ecdb79fe98/`（manifest `status=complete`，
    `capture_mode=raw`，`event_loss=0`，383 个快照条目全部带 sha256）
  - 采集脚本：`/Users/magic/Workspace/camoufox-reverse/artifacts/analysis/phase12/run-gsxt-passive.py`
  - 请求清单快照：`run-network-list.json`（52 请求：200×46、521×2、412×2、405×1、中止×1）

### 本轮观测到的挑战链（521→521→412→412→200）

| 序 | 状态 | 响应体 | 内容 | 证据（均相对会话目录 `raw/network/`） |
|---|---|---|---|---|
| 1 | 521 | 882 B | 一阶段 cookie 挑战：逐字符拼接 `__jsl_clearance_s=...` + `location.href` 重载 | `fc13564b20db46bea240ece635c9f869/` |
| 2 | 521 | 27 710 B | 二阶段 PoW：混淆 JS，`"ha":"sha256","tn":"__jsl_clearance_s","vt":"3600","wt":"1500"` | `3c9873311c424991aa491aa342f03870/` |
| 3 | 412 | 216 B | 「Environment Checking」页：外链 `/1eeb27c_268ab095e.js` + `/ctct_bundle_fdab54c7.js` | `394cea811608420c8d9249792325463c/` |
| 4 | 412 | 2 626 B | 瑞数式动态令牌页：meta 令牌 + 外链 `/cb4UuQtIG8gU/O3mmzGd2Kbfp.091bf33.js` + 内联 `_$a9()` | `d436c5c96f48485e98801db7f2985b95/` |
| 5 | 200 | 20 373 B | 真实首页 | `727f4fd31f81492c89d19dc3041fe36d/` |

时序（mcp-network `timestamp`，ms 级 wall-clock）：521@…165490 → 521@…165606 →
412@…167227（PoW 约 1.6s）→ 412@…174252（二阶段环境检测约 7s）→ 200@…175007。

Cookie/Set-Cookie 证据完整：521 步 `Set-Cookie: __jsluid_s`；挑战通过后全部 49 个后续
请求的 `Cookie` 头携带 `__jsluid_s` + `__jsl_clearance_s=1790360165.806|0|a7eLO6f9...` +
`CT_*` 令牌；412/200 步各设 `dUs8TeLcaHgjO=`、`JSESSIONID`、`tlb_cookie`；遥测端点
`POST /ctct/nwaf/waf.log`（405，请求体 5 808 B 加密 blob 已落盘
`2ae31499d740426db4f5af3adc132fc1/request.body`）。storage-state 快照 3 份
（`raw/state/*/storage-state.json`）含全量 cookie 终态。

## 1. 十条验收清单评分

| # | 验收问题 | 评分 | 依据与说明 |
|---|---|---|---|
| 1 | VM 程序本体全文在产物里吗？（所有 realm、所有通道） | ❌（下发本体 ✅，派生通道缺） | 网络下发通道全文齐：`ctct_bundle_fdab54c7.js` 1 287 712 B（5 份 sha256 全等 `9127c7ac…`，另有 1 份 `captured_partial` 截断副本已自描述）；`cb4UuQtIG8gU/O3mmzGd2Kbfp.091bf33.js` 378 344 B、`yvVnd5OBgXUn.091bf33.js` 305 939 B（`body_availability=captured`）；`1eeb27c_*.js` 三个变体 7 873/7 777/7 777 B；521/412 内联脚本在各自 response.body。缺口：ctct_bundle 内含 1 处 `new Function`，其**动态派生代码**不在任何产物中——raw profile 不捕获 eval/Function 通道，被动形态天然边界 |
| 2 | VM 执行了多少次循环、每次状态如何？ | ❌ | raw profile 不开 vm_loop，无任何 tick/状态产物。**被动形态天然边界**（对 gsxt 插桩即卡死，第十一阶段已定案，此项对该目标结构性不可得） |
| 3 | 派发形态裁决有 observed 级证据吗？ | ❌ | 产物内无动态裁决证据；但 ctct_bundle 全文支持**离线静态**裁决（69 个 `switch(`、390 个 `case`、53 个 `while(`、2979 个 `function`，集中于 `__CTCT_VM_UTILS__` 闭包）——静态裁决可行但未做，observed 级动态证据缺，天然边界 |
| 4 | opcode/handler 清单完整吗？ | ❌ | 无执行序列/参数证据，同 #2/#3 天然边界；离线静态分析可补（见第 3 节） |
| 5 | 加密参数落在哪个请求的哪个字段？有 initiator 栈吗？ | ❌（字段 ✅，栈缺） | 字段落地证据完整：`__jsl_clearance_s` 在 521 步由 JS 写入、自第 2 个请求起出现在全部 49 个请求的 Cookie 头；遥测字段在 waf.log 请求体。但 52/52 条 initiator 记录 `initiator_type=unknown`、`initiator_stack=null`（附 `correlation=unmatched` 诚实自描述）——**浏览器能力缺口**：不插桩页面世界时 route/Juggler 层未回填 JS 调用栈，可回流为引擎层能力需求 |
| 6 | 字段值与哪些环境采集点相关？ | ❌ | 无属性采集事件、无 taint 值事件（均需页面世界插桩），天然边界；加速乐采集点只能离线静态枚举 |
| 7 | redirect 链每个环节字节完整吗？缺的自描述了吗？ | ✅ | 521×2 / 412×2 / 200 五个环节 `request.json`+`response.body`+完整请求/响应头全在（52/52 有 request.json）。两处不完整均**自描述**：旁车 fetch 的 ctct_bundle 截断副本标 `body_availability=captured_partial, response_complete=false, failure=NS_BINDING_ABORTED`（且同文件另有 5 份全等完整副本）；baidu.com 中止请求标 `failure=NS_BINDING_ABORTED`。`capture-status.json` 预先声明「redirect bodies may be unavailable」，本轮实际全部捕获 |
| 8 | 原生事件有没有噪音混入、丢失？ | ✅ | 索引 `event_loss=0, event_loss_complete=true, event_loss_gaps=[]`；383 个快照条目全部带 sha256 记账。注：raw profile 本就不产出属性级事件，此条只裁决被动事件流（网络/存储/脚本）的完整性 |
| 9 | Worker/iframe/wasm 用了的都覆盖了吗？ | ✅（本轮目标未用） | 无 `.wasm` 请求，ctct_bundle 全文 0 处 `WebAssembly`；无 Worker/iframe 请求与 realm 证据。按验收注意点记「目标未在观测窗口实例化」，非 fail |
| 10 | JS 轨迹与原生事件时序能对齐吗？ | ❌ | 无 JS 轨迹可对齐（天然边界）；网络侧事件自带 ms 级 wall-clock `timestamp`，挑战链各步耗时可完整重建（见第 0 节时序） |

**汇总：✅ 3（#7 链路字节、#8 完整性、#9 无 wasm 目标）／❌ 7（#1 派生通道、#2 循环、#3 裁决、#4 opcode、#5 initiator 栈、#6 采集点关联、#10 时序对齐）／unknown 0。**

判读：❌ 集中在「VM 内部动态证据」一族——这正是第十一阶段定案的代价面：对连
Function.prototype.toString 都验的零容忍目标，动态证据与通过挑战二者不可兼得。
被动产物给出的答案是**链路层逆向**（挑战链、cookie 生命周期、遥测端点、时序）完备，
**VM 语义层逆向**只能走离线静态分析。

## 2. 缺口清单

| 缺口 | 分类 | 说明 |
|---|---|---|
| initiator 栈 52/52 unknown | **浏览器能力缺口** | 不插桩页面世界的前提下，可在 Juggler/引擎层回填发起栈；回流路线图（对应能力模型第 7 节「引擎级隐形 hook」观察项的子集，风险更低） |
| eval/Function 动态派生代码无产物 | 被动形态天然边界 | 捕获需页面世界 hook，零容忍目标不可用 |
| vm_loop 循环/状态/opcode 序列无产物 | 被动形态天然边界 | 同上，第十一阶段已定案 |
| 属性采集点事件、值级关联无产物 | 被动形态天然边界 | 同上 |
| JS 轨迹 ↔ 原生事件时序对齐 | 被动形态天然边界 | 无 JS 轨迹可对齐；网络时序坐标系仍在 |
| ~~ctct_bundle 截断副本~~ | 非缺口 | `captured_partial` 已自描述，且同 sha256 完整副本 5 份在产物中 |

## 3. 挑战 JS 离线静态分析可用性（专项验证）

**结论：足以做离线静态分析。** 实测依据：

| 文件 | 字节 | UTF-8 | 结尾完整性 | 结构探测 |
|---|---|---|---|---|
| `ctct_bundle_fdab54c7.js`（raw/scripts/5f3fd790…js） | 1 287 712 | ✅ 全文可解码 | ✅ 以 `return a0_0x2458();};})();` 闭合 | 69 switch / 390 case / 53 while / 2979 function / 字符串表 `_0x8f6f9f` / 命名空间 `__CTCT_VM_UTILS__`、`__ctct_bridge_shared` |
| `O3mmzGd2Kbfp.091bf33.js`（1ae003d1…js） | 378 344 | ✅ | ✅ 以置换表字面量 `]);}` 闭合 | `if($_ts.cd)` 守护 + `_$pn` 解码器形态 |
| `yvVnd5OBgXUn.091bf33.js`（9f64a1b7…js） | 305 939 | ✅ | ✅ 同上闭合 | 同族变体（与上者尺寸不同，属动态再生，两份并存正好供差分） |
| `1eeb27c_268ab095{e,d}.js` ×3 | 7 873/7 777/7 777 | ✅ | ✅ | 加载器/loader 变体，三轮差异可供轮换规律分析 |
| 521 PoW 内联脚本（network/3c987331…/response.body） | 27 710 | ✅ | ✅ `<script>…)</script>` 完整 | 含 `sha256`、`wt:1500` PoW 参数，可直接还原难度算法 |

字节保真：5 份独立捕获的 ctct_bundle sha256 全等（`9127c7ac82d16d17…`），证明文件
在传输/落盘链路零损坏；唯一截断副本已自描述且非唯一来源。混淆密度高（1.29MB 单行），
但完整性、可读性（合法 UTF-8 文本）、结构可探测性均满足静态分析前置条件；
反混淆工作量不在本条裁决范围内。

## 4. 零变异目标使用指引

### 4.1 什么目标该用被动 profile

- **已侦测到 FPT 替换的零容忍目标**：凡对页面世界函数级替换做语法级/构造性侦测的
  目标（加速乐为定案样本：连 `Function.prototype.toString` 替换都被二阶段环境检测
  静默卡死，Proxy 方案楔死 SpiderMonkey），JS 层插桩**不可隐形**，只能用被动形态。
- **裁决前置**：不要凭猜测归类。对新目标先跑一轮被动基线，再跑一轮最小插桩
  （单个无害 hook），用 `scripts/reverse-browser-instrumentation-divergence.py`
  双会话对比：裁决 `aligned`/`minor` 才可插桩；`diverged`（如 gsxt 请求数差 90.4%）
  即定案为零变异目标，回到被动 profile。
- 灰区目标（405 死循环类侦测）可先用 toString 伪装守护全家桶试插桩，
  仍以 divergence 裁决为准。

### 4.2 怎么配

```python
call("launch_browser", project_dir=project, headless=True, os_type="windows",
     locale="zh-CN", enable_trace=False, capture_profile="raw")
call("network_capture", action="start", capture_body=True)
call("navigate", url=url, wait_until="load")   # 不传 pre_inject_hooks，不开 vm_loop
```

- MCP server 启动参数必须**显式**带 `--proxy ""`（内网直连目标）——缺省会静默走
  本机 7890 代理导致 403（第十一阶段 1.3 节教训）。
- 完整可运行模板：`/Users/magic/Workspace/camoufox-reverse/artifacts/analysis/phase12/run-gsxt-passive.py`
  （PYTHONPATH 需含 `pythonlib` 与 `integrations/camoufox-reverse-mcp/src`，
  解释器 `/usr/local/bin/python3.12`；前后 `pkill -f 'Python.framework/Versions/3.12.*camoufox_reverse_mcp'`）。
- 等待策略：轮询 `document.title` 判定挑战通过（gsxt 约 9–14s），通过后再补 ≥10s
  遥测窗口，否则 waf.log 类遥测请求不进产物。
- 收尾：`list_network_requests` 落一份清单快照，再 `close_browser`。

### 4.3 被动产物的能与不能（预期管理）

- **能**：挑战链逐环节字节（含 412/521 中间态）、cookie 全生命周期（Set-Cookie 头 +
  后续请求 Cookie 头 + storage-state 终态）、挑战 JS 全文（保真，sha256 冗余校验）、
  遥测端点与请求体、ms 级网络时序、截断/中止全自描述。
- **不能**：VM 循环/opcode/派发动态证据、属性采集点、值级数据流、initiator JS 栈、
  eval 派生代码。这些需求一律转离线静态分析（第 3 节已验证产物够用）。
