# 第四阶段计划：产出物完备性（Worker 全覆盖 + 全量落盘 + 函数级追踪）

> 前置：第三阶段 VM 结构还原已完成（见 `2026-09-25-vm-structure-recovery-plan.md`，
> 报告 `docs/reverse8-phase3-opcode-hypotheses-2026-09-25.md`）。
> 本阶段主题是用户明确的定位：浏览器本身是要交付的能力，目标站点只是试金石；
> 验收标准是「仅凭浏览器产出物即可离线完成目标的逆向分析」。

**Goal:** 修复第三阶段盘点的三个量化缺口与未覆盖点：Worker realm 全量插桩
（重点，不能遗漏）、状态序列全量落盘、动态源码全文策略、chrome 噪音过滤、
函数级调用追踪（handler 语义升级的通道）。

**Architecture:** 全部改动保持分层纪律：Worker 插桩、采样策略、源码全文、
代码补丁通道是通用浏览器能力（integrations/hooks），不认识任何目标；
BotGuard 特定补丁与验证逻辑只进 `adapters/google_botguard/`。

## 缺口清单（来自第三阶段实测 session `07c3075f`）

| 缺口 | 实测数据 | 本阶段 Task |
|---|---|---|
| Worker realm 只记录不插桩 | 本轮 BotGuard 未用 Worker，一旦使用即全瞎 | Task 1（重点） |
| 状态序列只录 9%（911k 迭代 / 81k 快照） | MAX_STATES=10000 截断最热 6 循环 | Task 2 |
| redirect body 缺失 | 464 请求中 13 个无 body | Task 3（自描述化） |
| 动态源码只留有循环注入的全文 | 25 条仅 4 条全文 | Task 3 |
| chrome 噪音（resource://）混入 script 事件 | 未过滤 | Task 4 |
| 无函数级调用追踪，handler 语义停 inferred | — | Task 5 |

## Global Constraints

- 沿用前三阶段全部约束（project_dir 必填、raw 只追加、证据四级、分层隔离）；
- Worker 插桩不得破坏 Worker 语义（构造参数、onmessage 时序、terminate）；
- 所有 hook 改动先过 node 离线验证再实战；
- 每个 Task 结束三套测试回归：adapters、MCP、pythonlib（+tests/ 契约）。

## Task 1: Worker realm 全量插桩（重点）✅ 已完成

**Files:**
- Modify: `hooks/vm_loop_trace.js` — ①`SELF_SRC` 与注入的 tick 前缀全部改用
  `globalThis`（Worker 无 `window`，这是真实浏览器端到端才暴露的关键 bug）；
  ②Blob 构造器同步缓存全字符串 parts、`createObjectURL` 建 url→blob 映射；
  ③Worker 构造器真插桩（blob URL 同步取回源码→injectTicks→前置运行时→
  重打包；同源普通 URL 走同步 XHR；跨域/模块记 `worker-gap` 不伪造覆盖）；
  ④Worker 内运行时自动注册 drain 消息桥；⑤`drainAllAsync(timeoutMs)` 带超时
  聚合所有活 Worker，嵌套 Worker 由 Worker 侧桥递归（realm 路径
  `worker[0]/worker[0]`）
- Modify: `tools/vm_loop.py` — drain 走 `drainAllAsyncText`（回退链
  drainAllAsyncText→drainAllText→drainText），Worker 嵌套 realm 递归展平
- Test: node 离线 49 项全过（新增 Worker 全链路 7 项）
- Create: `tests/fixtures/worker_fsm.html` + `scripts/verify-worker-live.py`

**验收结果：** 真实 camoufox（reverse.9）端到端：主 Worker 3 次迭代状态
[1,2,3]、嵌套 Worker 3 次迭代状态 [1,5,9]、worker-gap 记录正确——全 PASS；
dola 实战回归 proof observed（2775 字符），Worker 插桩对无 Worker 目标
零影响。三套回归：MCP 110、adapters 42、pythonlib 356 全绿。

## Task 2: 状态序列全量落盘 ✅ 已完成

**Files:**
- Modify: `scripts/reverse-browser-google-login-smoke.py` — vm-loop 安装时
  `max_states_per_loop` 提升到 200000（148k 解码器循环全覆盖）
- Modify: `tools/vm_loop.py` — drain 输出每个循环与总体的 `coverage_pct`
  （recorded/iterations）；无快照变量的纯计数循环标 None（n/a）不计入分母
- Test: MCP 契约测试（截断 6.7%、无变量 n/a、嵌套 Worker realm 展平），
  112 项全绿

**验收结果（session `c03a5809`，proof observed）：** `truncated` 循环从 6 个
降为 **0**；最热解码器循环 148 595/148 595 全量落盘；artifact 12.6MB
可接受。总覆盖率数字 47.8% 的构成已自描述：剩余全是「无快照变量的纯计数
循环」（位流读取器等 `for(G>0)` 形态），非截断。

## Task 3: redirect 链自描述 + 动态源码全文策略 ✅ 已完成

**Files:**
- Modify: 网络捕获层——redirect 无 body 时写入 redirect 链元数据
  （from/to/status/location），缺口从「静默缺失」变「自描述缺失」
- Modify: `hooks/vm_loop_trace.js` — `recordSource` 对 ≤256KB 的源码一律
  保留全文（不再以 loops_injected>0 为条件）
- Test: 契约 + node 用例

## Task 4: chrome 噪音过滤 ✅ 已完成

**Files:**
- Create: `pythonlib/camoufox/trace_filter.py` — trace jsonl 事件过滤
  （`resource://`/`chrome://`/`about:` 前缀），通用能力
- Test: `pythonlib/tests/test_trace_filter.py`

## Task 5: 通用代码补丁通道（函数级追踪的载体）✅ 已完成

**实战验收（session `b558774e`，proof observed 2785 字符）：**
derived/handler-counts.json 产出注册/派发频次表——opcode 140 注册
121 644 次、170 注册 3 084 次（佐证「可追加型 handler」假设）、210 派发
3 006 次；level=observed。冒烟脚本新增 `--handler-counts` 开关。
adapters 48、MCP 112、node 56 全绿。

## Task 6: 收尾 ✅ 已完成

- 三套回归全绿 + dola 实战复测（proof observed）；
- 报告 `docs/reverse8-phase4-artifact-completeness-2026-09-25.md`：
  缺口清单逐项给「修复前 → 修复后」对照；
- 计划勾选；git 不主动提交。

**结果：** adapters 48 / MCP 112 / pythonlib 363 / node 56 全绿；
dola 实战三轮（p4 / p4t2 / p4t5）proof observed、0 事件丢失；
handler 频次表 observed 级产出。报告已写。

## 执行顺序

Task 1（Worker，最重）→ Task 2 → Task 3 → Task 4 → Task 5 → Task 6。
