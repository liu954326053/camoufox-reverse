# 第四阶段：产出物完备性（缺口逐项修复对照）

日期：2026-09-25 ｜ 计划：[2026-09-25-artifact-completeness-plan](superpowers/plans/2026-09-25-artifact-completeness-plan.md)

定位（用户明确）：浏览器本身是要交付的能力，dola→Google 登录 BotGuard 只是
试金石；验收标准是「仅凭浏览器产出物即可离线完成目标逆向」。本阶段修复第三
阶段盘点的量化缺口与未覆盖点。

实战 session：
- `dola-google-vm-20260925-p4/runs/…`（Task 1 Worker 回归，proof observed）
- `dola-google-vm-20260925-p4t2/runs/c03a5809359d41d1a16e4cafe53f9637`（Task 2 全量落盘，proof observed）
- `dola-google-vm-20260925-p4t5/runs/b558774e678449cf90f02d44c531dd31`（Task 5 handler 计数，proof observed 2785 字符，summary status=passed）

## 缺口对照表（修复前 → 修复后）

| 缺口 | 修复前（第三阶段实测） | 修复后 | 证据 |
|---|---|---|---|
| **Worker realm 只记录不插桩** | 一旦目标把 VM 放进 Worker 即全瞎 | Blob/createObjectURL/Worker 构造器全链路插桩；Worker 内 drain 消息桥；嵌套 Worker 递归聚合（realm 路径 `worker[0]/worker[0]`）；拿不到源码记 `worker-gap` 不伪造覆盖 | 真实 camoufox e2e：主 Worker [1,2,3]、嵌套 Worker [1,5,9] 全 PASS（`scripts/verify-worker-live.py`）；dola 回归 proof observed |
| **状态序列只录 9%** | 911k 迭代 / 81k 快照，最热 6 循环截断 | `max_states_per_loop=200000`，截断循环 6→**0**，解码器 148 595/148 595 全量落盘；每循环与总体 `coverage_pct` 自描述（纯计数循环标 n/a 不计分母） | session `c03a5809`，artifact 12.6MB |
| **redirect body 静默缺失** | 464 请求中 13 个无 body，原因不可辨 | 3xx+Location 时 `body_availability="redirect"` + `redirect_location` 自描述；非 redirect 失败保持 `unavailable` 不误标 | pythonlib 契约测试 2 项 |
| **动态源码只留有循环注入的全文** | 25 条仅 4 条全文 | ≤256KB 一律保留全文（纯数据/无循环脚本也可离线分析），超限留前缀 | node 用例 |
| **chrome 噪音混入 script 事件** | 未过滤，分析需手工排除 | `pythonlib/camoufox/trace_filter.py` 通用过滤（`resource://`/`chrome://`/`about:`），raw 只追加、产出 derived 视图 + 按前缀统计 | p4t2 实测：最热 trace 1822→390 行（78% 噪音）；5 项契约测试 |
| **无函数级调用追踪** | handler 语义停 inferred | 通用代码补丁通道：`{{CODE_PATCHES}}` 正则→替换，应用在 tick 插桩之后，无循环源码也应用；产物写 `__mcp_vm_counters` 随 drain 跨 realm 合并 | node 7 项新用例全过；adapters fixture 6 项 |

## handler 频次表（Task 5 实战产出，observed）

`derived/handler-counts.json`（session `b558774e`）：

- **注册**：opcode 140 注册 121 644 次、170 注册 3 084 次——直接佐证第三
  阶段「140/170 为可追加型 handler（列表/回调收集）」假设，inferred →
  升级候选；33/61/72/83/86/95/100/259/265/283 各注册 4 次（一次性初始化）。
- **派发**：210 派发 3 006 次（最热 handler，TN 特殊包装组成员）、
  166 派发 6 次，14/69/281/282/283/366 各 2 次。
- 频次数量级差异本身是结构证据：注册次数 ≫ 4 的 handler 承载程序数据
  的反复装载，派发次数高的 handler 是程序主路径。

补丁定义在 `adapters/google_botguard/handler_patch.py`（目标特定，函数名
按 build 参数化）；通道在 `hooks/vm_loop_trace.js` + `tools/vm_loop.py`
（通用，不认识任何目标）。冒烟脚本新增 `--handler-counts` 开关。

## 本阶段工具改动清单

- `hooks/vm_loop_trace.js`：Worker 全链路插桩（globalThis 化、Blob 缓存、
  Worker 构造器、drain 桥、drainAllAsync）；`recordSource` ≤256KB 全文；
  `{{CODE_PATCHES}}` 通道 + `patches_applied` 元数据；drain 回收 counters。
- `tools/vm_loop.py`：drainAllAsyncText 回退链 + 嵌套 realm 递归展平；
  `coverage_pct` 自描述；`code_patches` 参数；counters 跨 realm 深合并。
- `pythonlib/camoufox/reverse_runtime.py`：redirect body 缺口自描述化。
- `pythonlib/camoufox/trace_filter.py`：chrome 噪音过滤（新建）。
- `adapters/google_botguard/handler_patch.py`：handler 计数补丁 +
  `write_handler_counts`（新建）。
- `scripts/reverse-browser-google-login-smoke.py`：`--handler-counts` 开关。

## 测试状态（全绿）

| 套件 | 结果 |
|---|---|
| adapters | 48 passed |
| MCP | 112 passed, 2 skipped |
| pythonlib | 363 passed, 5 skipped |
| node 离线（vm_loop 注入器） | ALL PASS（56 项，含补丁通道 7 项） |
| tests/ 契约 | unittest 3 文件 OK；pytest 7 passed, 4 skipped |
| 真实 Worker e2e | 全 PASS（嵌套 Worker 状态聚合） |
| dola 实战复测 | proof observed ×3 轮（p4/p4t2/p4t5），0 事件丢失 |

## 已知边界（下一阶段登记）

- Worker 插桩目前依赖同源/可同步取源码；跨域模块 Worker 记 `worker-gap`
  （自描述，不伪造覆盖）。
- handler 补丁函数名按 build 参数化；混淆改名后计数为空（level=gap），
  需按新 build 实测名调整——这是补丁通道的固有特性，已在产出物注明。
- git 未主动提交（四阶段累计改动按用户惯例留给用户审阅）。
