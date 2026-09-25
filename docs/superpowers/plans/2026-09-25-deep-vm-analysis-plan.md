# 第二阶段计划：深度执行与 VM 分析

> 前置：第一阶段基础平台已完成（见 `2026-09-23-reverse-analysis-browser-foundation.md`），
> Google 登录 VM 实战检验已通过（见 `docs/reverse8-google-login-vm-validation-2026-09-25.md`）。
> 本计划按基础计划"Separate Follow-up Plan"的要求，在改动 SpiderMonkey 之前产出。

**Goal:** 把第一阶段采集的原始证据升级为可回溯的 VM 执行证据：从 BotGuard proof 值反推到 VM 程序、解释器循环和宿主环境输入，并输出浏览器与外部复现实现的首个可核验分歧位置。

**Architecture:** 新增独立的 `adapters/` 目录承载目标特定的 VM 适配器（设计文档"代码组织"边界：目标适配器不得反向污染浏览器核心）。Google/BotGuard 相关的 `MI613e`、`B4hajb`、proof 字段只允许出现在 `adapters/google_botguard/`。浏览器核心只新增通用的深度探针能力（native trace profile、解释器循环插桩），不认识任何具体目标。

**已确认的事实（来自 reverse.8 实战 session `a12c51c98f29454680f2c1b9feb777bc`）：**

- BotGuard VM 内核 `botguard.bg`/`botguard.a` 内嵌在 identifier 页 HTML，不经 `/js/bg/` 网络加载；
- VM 程序以 base64 内嵌在页面 `AF_initDataCallback` 数据块中，程序体在 `//` 分隔符之后，由 `atob` 解码；
- 解释器是 `while(true)` + 数值状态派发（`N` 状态变量，`A` 辅助状态），同步执行；
- 标识提交触发 `batchexecute?rpcids=MI613e`，proof 是 `f.req` 解码后嵌套数组中 2453 字符的 `!` 前缀字符串；
- `B4hajb` 在当前流程未出现，属更靠后步骤；
- 原生 PropertyTracer 77 hooks、mw: 主世界执行、raw 网络证据（含发起栈）均已验证可用。

## Global Constraints

- 沿用第一阶段全部约束：project_dir 必填、raw 只追加、不提交真实账号凭据、不绕过验证码；
- 适配器只读 raw 证据写 derived/，绝不修改 raw/；
- 值生成链节点必须带证据等级（`observed`/`call-linked`/`inferred`/`gap`），禁止时间相近即认定数据依赖；
- 原生改动保持 protocol-v1 兼容与 77 个既有 hook 语义不变；新增 hook 只增不改；
- 所有 native 改动必须先写浏览器外测试（注入器/契约测试），再编译验证；
- 每个 Task 结束跑通：pythonlib、integrations MCP、adapters 三套测试。

## File Map

- Create: `adapters/google_botguard/` — VM 程序提取、proof 定位、执行链关联（纯 Python，离线）。
- Create: `adapters/google_botguard/tests/` — fixture 驱动单元测试。
- Create: `integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/hooks/vm_loop_trace.js` — 通用解释器循环插桩（状态变量、迭代计数、耗时），不认识 BotGuard 字段。
- Modify: `integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/jsvmp.py` — 新增循环 trace 安装入口。
- Modify: `additions/camoucfg/PropertyTracer.cpp/hpp`（后续 Task）— 深度 profile 的脚本执行帧事件，先契约测试再编译。
- Create: `adapters/google_botguard/divergence.py`（后续 Task）— 浏览器证据与外部实现的逐步差分。

## 观测分层补充（相对设计文档的落地）

设计文档"深度模式"要求的 VM dispatcher/虚拟 PC/opcode/handler，分两步落地：

1. **JS 层（本计划 Task 2）**：对页面内解释器循环做结构化插桩，记录每次派发的状态值序列（等价于虚拟 PC 轨迹）与循环进出时间。扰动可测量、可开关。
2. **原生层（本计划 Task 4）**：SpiderMonkey 脚本级进入/退出事件与帧信息，校验 JS 层轨迹的完整性（是否有未被插桩覆盖的执行）。

## Task 1: BotGuard VM 适配器 v1（离线证据关联）

**Files:**
- Create: `adapters/google_botguard/__init__.py`
- Create: `adapters/google_botguard/program.py` — 从 session raw 证据提取 VM 程序与程序 hash
- Create: `adapters/google_botguard/proof.py` — 从 batchexecute 调用记录定位 proof
- Create: `adapters/google_botguard/chain.py` — 生成 `derived/vm-evidence.json`
- Test: `adapters/google_botguard/tests/`

**Interfaces:**

```python
from adapters.google_botguard import BotGuardProgram, ProofToken, build_vm_evidence

program = BotGuardProgram.extract(session_dir)   # program.bytes, program.sha256, program.source_artifact
proof = ProofToken.locate(session_dir)           # proof.value, proof.rpcid, proof.request_id, proof.path_in_f_req
report = build_vm_evidence(session_dir)          # 写入 session derived/vm-evidence.json
```

Required behavior:

- 程序提取只从 `raw/` 读取：扫描含 `botguard` 的 `AF_initDataCallback` 数据块，取 `//` 后 base64 解码为程序字节；找不到时返回带 `gap` 等级的结果而不是猜测；
- proof 定位只认 `accounts.google.com` 的 `batchexecute` 调用记录，递归解析 `f.req` 嵌套 JSON，proof 定义为长度 > 500 的 `!` 前缀字符串；多个候选时全部列出并标记 `ambiguous`，不挑选；
- 每个关联节点标注证据等级；程序 hash ↔ proof ↔ request_id ↔ 发起栈的关联写入 `derived/vm-evidence.json`；
- 对不存在 botguard 证据的 session 返回空结果加 `gap` 说明，不抛异常。

**Steps:**

- [x] Step 1: fixture 测试先行（页面 HTML 片段、call 记录、f.req 嵌套）
- [x] Step 2: 实现 program/proof/chain
- [x] Step 3: 对真实 session `a12c51c98f29454680f2c1b9feb777bc` 离线运行，程序 hash 与 proof 均能产出
- [x] Step 4: 三套测试回归 + 提交

## Task 2: 解释器循环 JS 插桩（通用，非 BotGuard 专用）

**Files:**
- Create: `hooks/vm_loop_trace.js` 与 `tools/jsvmp.py` 安装入口
- Test: `integrations/camoufox-reverse-mcp/tests/test_vm_loop_trace.py`

**要点：**
- 用户提供解释器函数的特征（包含 `while(true)` 与状态派发的函数），插桩记录每次迭代的状态变量值序列、迭代计数、进出时间；
- 支持 `persistent`，导航前安装；扰动测量：空转 overhead 写入结果；
- 不解析状态值语义（语义属适配器），只产出原始轨迹到 session raw/。

**Steps:**
- [x] Step 1: loop_rewriter（esprima 静态注入）+ tick 运行时 + `vm_loop_trace` 工具，17 测试绿
- [x] Step 2: 实战发现解释器不走 HTML 明文——动态通道六 hook（eval/Function 族/TT createPolicy/script 元素/Worker/setTimeout 字符串），node 离线验证 21 项全过
- [x] Step 3: 定位 BotGuard realm 策略：bscframe iframe 内独立 realm 执行 VM；运行时自我复制装入 iframe + drainAll 跨 realm 聚合
- [x] Step 4: 实战验证通过——session `e36c9c6186bf42a990db92b0874778dd`：frame[0] realm 捕获 VM 派发循环 148068 次迭代、状态快照序列落盘，proof 正常生成（2785 字符），tick 开销 348 ns/次；三套测试 109+6+356 全绿

## Task 3: proof 生成链回溯

- 用 Task 1 的程序字节 + Task 2 的轨迹 + 原生 PropertyTracer 事件，建立 proof 值的反向链节点（observed 级别：proof 在请求体、程序字节、环境输入快照）；
- 输出链到 `derived/`，标注 gap 段落。

**Steps:**
- [x] Step 1: `trace_link.py`（VmExecution 汇总 raw/vm-loop、EnvInputs 汇总原生 trace），fixture 测试先行
- [x] Step 2: `chain.py` 接入 vm_execution / environment_inputs 节点，gap 语义保留
- [x] Step 3: 对实战 session `e36c9c6186bf42a990db92b0874778dd` 离线运行：链 5 节点全部 observed（request_field / initiator_stack / vm_program / vm_execution 148068 次派发迭代 / environment_inputs 1720 条属性读取），写回 `derived/vm-evidence.json`；三套测试 10+109+356 全绿

## Task 4: SpiderMonkey 深度 hook（需重编译，单独排期）

- 先写注入器与契约测试（77 hooks 不变，新增 script enter/exit 事件）；
- `scripts/build-reverse-browser.sh` 增量编译 reverse.9；
- 受控 smoke + Dola→Google 复测。

**Steps:**
- [x] Step 1: `PropertyTracer.hpp` 新增 `ScriptExecGuard`（RAII，enter k=3 / exit k=4，禁用热路径一次原子加载）；注入器新增 `_apply_script_exec`（锚点 `ExecuteState state(...)`，幂等/原子/fail-closed），独立于 77 个 DOM hook 记账
- [x] Step 2: 契约测试 6 项（新鲜注入/幂等/verify 模式/锚点歧义 fail-closed/开关/头文件契约），`tests/test_inject_trace_to_source.py` 20 项全过；真实树 check/apply/verify 通过
- [x] Step 3: reverse.9 版本 bump（upstream.sh / reverse_compat.py / capabilities.json / install-camoufox-reverse.py / 三处测试），新增 `script_exec_events` 能力位；增量编译+打包+安装成功（首次编译暴露命名空间错误 `camou::PropertyTracer::ScriptExecGuard`→`camou::ScriptExecGuard`，已修）
- [x] Step 4: Dola→Google 复测通过（session `e98ca169b4694055a796dd7c19ab45ef`）：proof observed、VM 循环 130 个/335 062 次迭代、script enter/exit 4973/4973 平衡、覆盖 identifier 页面脚本，77 属性 hook 事件正常

## Task 5: 浏览器与外部实现的首个分歧报告

- `adapters/google_botguard/divergence.py`：对齐浏览器轨迹与外部 VM 复现实现的状态序列，输出首个分歧的虚拟 PC、输入快照与证据等级；
- 外部实现不存在时输出 `gap`，不伪造对比。

**Steps:**
- [x] Step 1: `divergence.py` 实现——浏览器侧取派发循环快照首元素序列，`compare_sequences` 逐位对齐（aligned / aligned-prefix / diverged / gap 四态），写 `derived/vm-divergence.json`
- [x] Step 2: fixture 测试 7 项全过（序列提取/对齐/前缀/首个分歧/双侧 gap/坏输入拒绝）
- [x] Step 3: 对实战 session `e98ca169b4694055a796dd7c19ab45ef` 运行：浏览器派发状态序列 10 000 个（33→14→4→70→75→92→8→11…），外部复现实现不存在 → 按契约输出 `gap`，明确标注"comparison not fabricated"

## 验收标准

1. 对实战 session 离线运行适配器，产出程序 hash、proof 值与关联链，全部带证据等级；
2. 开启循环插桩重跑实战链路，proof 生成期间的解释器状态轨迹落盘且扰动可量化；
3. 原生深度 hook 编译通过后，JS 轨迹与原生事件可互相校验覆盖度；
4. 分歧报告至少输出一个可核验位置或明确的 `gap`；
5. 全流程无 GUI，三套测试全绿。

**验收核对（2026-09-25 全部通过）：**

1. ✅ `derived/vm-evidence.json`：链 5 节点 observed，程序 sha256 与 proof 均带证据等级；
2. ✅ session `e36c9c6186bf42a990db92b0874778dd`：148 068 次派发迭代落盘，tick 开销 348 ns/次；
3. ✅ session `e98ca169b4694055a796dd7c19ab45ef`（reverse.9）：JS 侧 130 循环/335 062 迭代，原生侧 script enter/exit 4 973/4 973 平衡且覆盖 identifier 页面与 eval 通道，属性事件 13 类对象 1 800+ 条，两通道互证；
4. ✅ `derived/vm-divergence.json`：外部实现缺失时输出 `gap`（不伪造）；对齐逻辑经 fixture 验证可输出首个分歧位置；
5. ✅ 全程 headless；unittest 26 + adapters 17 + MCP 109 + pythonlib 356 全绿。
