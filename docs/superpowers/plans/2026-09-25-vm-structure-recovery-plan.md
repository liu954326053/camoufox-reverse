# 第三阶段计划：VM 结构还原（解释器定位与 opcode 语义）

> 前置：第二阶段深度执行与 VM 分析已完成（见 `2026-09-25-deep-vm-analysis-plan.md`，
> 总结 `docs/reverse8-phase2-deep-vm-analysis-2026-09-25.md`）。
> 本阶段把第二阶段采集的 148k 级状态序列升级为 VM 结构还原：
> 定位解释器派发形态、还原程序解码链、产出带证据等级的 opcode 语义假设。

**Goal:** 从循环插桩轨迹与动态源码中还原 BotGuard VM 的执行结构——字符串解码器、
程序解压器、排表（handler 表）构建、加密原语各自的循环角色确认，并回答
「opcode 派发在哪里」这个第二阶段遗留问题，最终产出 opcode 语义假设报告。

**Architecture:** 不新增目录。分析逻辑进 `adapters/google_botguard/`（目标特定，
fixture 驱动）；通用插桩能力仍只在 `integrations/camoufox-reverse-mcp/hooks/vm_loop_trace.js`
（不认识 BotGuard 字段）；原生层本阶段不改。

## 第三阶段开局分析已确认的事实（session `e98ca169` + 复测 `266b076a`）

- 第二阶段快照里 `distinct=[None]` 的高迭代循环全部是**辅助循环**（16 轮加密 `uc`、
  UTF-8 编码 `wC`、字节拷贝），None 的成因有二：`for(init;;)` FSM 无测试表达式
  导致自动识别落空；冒烟脚本硬编码 `state_vars=["N","A"]` 污染所有快照。
- 148428 次迭代循环是 `tD` **字符串解码器**（`charCodeAt` 逐字符 + 定长 5 状态 FSM
  周期，148428/5 ≈ 29686 ≈ VM 程序字节数 29627），不是 opcode 派发。
- 全源码扫描**不存在**单一巨型 opcode switch（最大 switch 仅 31 case，是 DOM 工具）。
- hook 增强（本阶段 Task 0，已完成）后复测捕获到新结构：
  - `28067` 循环 56893 次迭代：`(h+(TN(K,128,12)|0))%u; Y.push(D[h])` ——
    **排表/采样循环**（疑似 handler 表置换），7 状态数据依赖；
  - `29451` 循环 775 次迭代 20 态：内含位流读取器
    `P=function(gj,nA){for(;z<gj;)D|=sR(a)<<z,z+=8;...}` 与 `u=(P(3)|0)+1,b=P(5)` ——
    **程序解压器**（类 LZ/Huffman 表构建）；
  - `44197` 循环 8722 次、周期 34：`Z.push(Z.rA[X&l]^e)` —— XOR 流加密循环；
  - `62333` 循环 52 次 32 态：`Tk(8,28,0,atob(n))` + `this.C=len<<3` ——
    哈希初始化（bit-length padding 形态）。
- **工作假设**：BotGuard 派发不是集中式 switch 循环，而是 handler 表（`28067` 置换
  产物）+ threaded 调用（handler 末尾直接调用下一条），这解释了为什么没有热点
  派发循环。Task 2 验证此假设。

## Global Constraints

- 沿用第一/二阶段全部约束（project_dir 必填、raw 只追加、证据等级四级、
  适配器只读 raw 写 derived）；
- 插桩改动必须先过 node 离线验证（`tests/verify_vm_loop_inject.node.js`），
  再实战复测；
- 每个 Task 结束跑通三套测试：adapters、MCP、pythonlib（unittest 文件用
  `python3.12 -m unittest` 跑，不走 pytest）。

## Task 0（已完成，记账用）：FSM 状态变量识别增强

- Modify: `hooks/vm_loop_trace.js` — 新增 `fsmVarAfter`：`for(init;;)` /
  `while(true)` 无测试循环，从循环体头部第一个 `if(X==数字)`/`switch(X)`
  识别判别变量；for 循环要求 X 在 init 中被赋值（含逗号表达式嵌套），防误抓。
- Modify: `scripts/reverse-browser-google-login-smoke.py` — 移除硬编码
  `state_vars=["N","A"]`，全靠自动识别。
- 验证：node 离线 34→42 项全过；MCP 109 全绿；实战复测 `266b076a` 捕获
  28067/29451/44197/62333 四个新结构循环。

## Task 1: 循环角色分类器（离线）✅ 已完成

**Files:**
- Create: `adapters/google_botguard/loop_roles.py` — 输入 vm-loop artifact +
  dynamic source，输出每个循环的角色分类与证据
- Test: `adapters/google_botguard/tests/test_loop_roles.py`（fixture：复测轮
  `266b076a` 的 artifact 摘录）— 12 项全过

**分类规则（可解释、证据分级）：**
- 定长周期 FSM 周期 × 迭代数 ≈ 程序字节数 + charCodeAt → `string-decoder`（observed）；
  周期判定容忍 ≤16 状态的一次性初始化 prologue（真实数据有 5 状态 prologue）；
- 循环窗口含位流读取器（`for(;z<n;)D|=f(a)<<z,z+=8`）或解压头（`P(3)+1,P(5)`）
  → `decompressor`（observed）；
- 循环体含 `Y[f](D[h])` 取模索引采样 → `table-permutation`（observed）；
- XOR 流 push 或旋转移位轮函数（`>>>`/`<<24`/`^=` 混合）→ `cipher`（observed）；
- 变长位掩码累积（`|=(…&(1<<M)-1)<<`）→ `bit-reader`（observed，程序位流解密）；
- 混淆空转循环包裹立即返回（`while(0==![]){return…}`）→ `string-accessor`；
- 只有周期性无签名 → `periodic-fsm`（inferred）；其余 → `unknown`（gap，
  附状态直方图）。

**验收结果（session `266b076a`，程序 29795 字节）：** 热度前 14 的循环全部
observed 级归类——string-decoder 148910（148910/5=29782≈29795）、
bit-reader 88134、cipher 66691/33601/8722、table-permutation 56893、
string-accessor 29956、decompressor 775。产物
`derived/loop-roles.json`（unknown 105 条均为低频辅助循环）。

## Task 2: 派发形态裁决（threaded vs 集中式）✅ 已完成

**Files:**
- Create: `adapters/google_botguard/dispatch.py`
- Test: `adapters/google_botguard/tests/test_dispatch.py`（6 项全过）

**裁决结果（session `266b076a`，`derived/dispatch-verdict.json`）：**
`form = table-indirect`，三条 observed 级证据，四个动态源码一致：

1. `handler-table-registrar`：`Du(l,p,c)` 把 handler 按数值 id 写入 `l.Y[p]`
   （140/170 可追加、十个特定 id 走 `TN(p,43,16,…)` 包装、其余 `TN(p,43,3,…)`；
   p==232 时重置密钥流 `l.W`）；
2. `create-dispatch`：`t(l,p){c=p.Y[l];…c.create()}` —— 派发经表项 `.create()`
   间接调用，全源码仅 6 个调用点，分散在函数内，无热点派发循环；
3. `scope-stack`：`TY.push(Y.slice())` —— VM 调用帧压/弹 handler 表。

旁证：全源码最大 switch 仅 31 case（DOM 工具），不存在集中式 opcode switch。
计划原先的二分（threaded/centralized）细化为 table-indirect（比 threaded
多一层 `create()` 包装，但同样没有集中派发循环）。排表循环（Task 1 的
table-permutation，56893 次迭代）的角色由此闭环：它采样置换的就是 handler
表 `Y` 的子表，产物经 `Du` 注册回表。

## Task 3: tick 相对时间戳（原生↔VM 时序关联）✅ 已完成

**Files:**
- Modify: `hooks/vm_loop_trace.js` — `{{TICK_TIMES}}` 模板开关（默认关）；
  开启后每个循环记录首 tick 的 `performance.now()` 基准，drain 时附 `times`
  数组（相对首 tick 毫秒，µs 精度，与 `states` 平行对齐）
- Modify: `tools/vm_loop.py` — `vm_loop_trace` 新增 `tick_times` 参数
- Modify: `scripts/reverse-browser-google-login-smoke.py` — 实战开启 tick_times
- Test: node 离线新增 4 用例（46 项全过）；MCP 模板契约 2 项（110 全绿）

**验收结果（session `07c3075f`，proof observed）：** 141/141 循环带 times；
原生 script 事件的 `w` 字段（epoch µs）与循环 `first_ts`（epoch ms）+
`times`（相对 ms）同一 wall-clock 坐标系。VM 主窗口
（1790323383719–1790323383853 ms，含 149850 次解码器循环）内恰好 14 个原生
script 事件（`debugger eval code > eval` 通道 7 enter + 7 exit 平衡）——
JS 层轨迹在该窗口无未覆盖执行。

## Task 4: opcode 语义假设报告 ✅ 已完成

**Files:**
- Create: `adapters/google_botguard/opcode_report.py` — 汇总 Task 1-3 产物
  （7 项 fixture 测试全过）
- Create: `docs/reverse8-phase3-opcode-hypotheses-2026-09-25.md`

**产物：** `derived/opcode-hypotheses.json`（session `07c3075f`）：六类循环
角色语义 observed 级 28/141（热点全覆盖，其余为低频辅助循环）；dispatch
table-indirect；4 条 handler 假设（232 inferred 密钥流重置、140/170
inferred 可追加型、166 组 gap）。纪律守住：handler 语义无一标 observed。

## Task 5: 收尾 ✅ 已完成

- 已知边界已登记进报告「已知边界」节（Worker realm 只记录不插桩、
  原生 script 事件 chrome 噪音未过滤、times 随 MAX_STATES 截断、
  状态数/函数名每 build 随机化）；
- 回归全绿：adapters 42、MCP 110、pythonlib 356、tests/ unittest
  TestCase 文件全 OK、tests/ pytest 式契约 28 passed（需
  `PATH=/usr/local/bin:$PATH` + `--noconftest`：bare python3 缺 orjson、
  conftest 卡 pixelmatch，均为既有环境怪癖，非本阶段回归）；
- 计划逐 Task 勾选完毕；git 改动未提交（按约定不主动提交）。

## 执行顺序

Task 0（已完）→ Task 1 → Task 2 → Task 3 → Task 4 → Task 5。
Task 3 与 Task 2 可互换（若 Task 2 静态分析即裁决成功，Task 3 仍做，
因为它本身是设计文档要求的关联能力）。
