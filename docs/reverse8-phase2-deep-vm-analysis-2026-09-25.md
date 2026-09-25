# 第二阶段总结：深度执行与 VM 分析（2026-09-25）

承接第一阶段（Dola→Google 登录链路证据捕获），本阶段把观测能力推进到
BotGuard VM 的执行内部。计划文档：
[2026-09-25-deep-vm-analysis-plan](superpowers/plans/2026-09-25-deep-vm-analysis-plan.md)。

## 五个任务全部完成

### Task 1：BotGuard 适配器 `adapters/google_botguard/`
- `program.py`：从 identifier 页 AF_initDataCallback 提取 VM 程序 base64 blob
  （严格整体解码 → `//` 尾部 → urlsafe 兜底，最小 1000 字节），sha256 落证。
- `proof.py`：从 batchexecute 的 `f.req` 递归解析定位 proof（`!` 前缀、>500 字符），
  多候选标 ambiguous 不挑选。
- `chain.py`：`derived/vm-evidence.json`，每个节点带证据等级。

### Task 2：解释器循环插桩（本阶段核心突破）
四轮定位：
1. HTML 静态改写无效——49 处 `while(` 全是 `\u003d` 转义的函数字符串；
2. eval/Function hook 静默——CSP `require-trusted-types-for 'script'`，
   动态脚本走 Trusted Types policy；
3. 抓到加载器后仍静默——VM 在 `_/bscframe` iframe 独立 realm 执行；
4. 运行时自我复制装入 iframe realm + 跨 realm 聚合 drain 后，捕获
   **148 068 次迭代的 VM 派发循环**，状态序列完整，proof 正常生成。

产物：`hooks/vm_loop_trace.js`（六类动态通道 hook + while/for 双形态注入 +
realm 自适应降级）、`tools/vm_loop.py`（`vm_loop_trace` MCP 工具）、
`utils/loop_rewriter.py`（静态 route 改写）。详见
[Task 2 验收记录](../reverse8-vm-loop-trace-task2-2026-09-25.md)。

### Task 3：proof 生成链回溯
`trace_link.py` 汇总 VM 轨迹与原生 PropertyTracer 事件；实战 session 上链
5 节点（request_field / initiator_stack / vm_program / vm_execution /
environment_inputs）全部 observed。

### Task 4：SpiderMonkey 深度 hook + reverse.9
- `ScriptExecGuard`（RAII）注入 `js::ExecuteKernel`，script enter(k=3)/
  exit(k=4) 事件；独立于 77 个 DOM 属性 hook 记账。
- reverse.9 编译、打包、安装完成；实战复测 enter/exit 4 973/4 973 平衡，
  覆盖 identifier 页面脚本与 eval 通道，77 属性 hook 无回归。

### Task 5：分歧报告
`divergence.py`：浏览器派发状态序列（10 000 个快照）与外部复现实现逐位对齐，
四态（aligned / aligned-prefix / diverged / gap）；外部实现当前不存在，
按契约输出 gap 并注明"comparison not fabricated"。

## 测试与证据

- unittest 26（注入器/安装器/构建契约）+ adapters 17 + MCP 109 +
  pythonlib 356 全绿；node 离线注入验证 22 项全过。
- 关键 session：
  - Task 2/3：`runs/e36c9c6186bf42a990db92b0874778dd`（148k 迭代轨迹）
  - Task 4/5：`runs/e98ca169b4694055a796dd7c19ab45ef`（reverse.9 复测）
  - 均位于 `artifacts/tasks/reverse-browser-design/live-smoke/dola-google-vm-20260925/`

## 后续方向（第三阶段候选）

- Worker realm 插桩（本轮 BotGuard 未用 Worker，只有记录通道）；
- 基于 148k 状态序列的 VM opcode 语义还原（适配器 SDK）；
- Go 复现实现接入 divergence.py，产出首个真实分歧位置；
- script exec 事件的 chrome 噪音过滤（resource:// / moz-extension://）。
