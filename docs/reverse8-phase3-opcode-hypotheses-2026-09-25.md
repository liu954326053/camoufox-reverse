# 第三阶段：BotGuard VM 结构还原与 opcode 语义假设

日期：2026-09-25 ｜ 计划：[2026-09-25-vm-structure-recovery-plan](superpowers/plans/2026-09-25-vm-structure-recovery-plan.md)

实战 session：
- `dola-google-vm-20260925-p3/runs/266b076a43ed4c61a1f7aed58a3c2365`（hook 增强后首轮，proof observed）
- `dola-google-vm-20260925-p3t3/runs/07c3075f8dc44ff39b298c3327c1fef3`（tick 时间戳轮，proof observed，29983 字节程序）

## 一、执行流水线全貌（全部 observed 级）

BotGuard VM 在该 build 的执行由六类结构化循环组成，按热度排序：

| 迭代数 | 角色 | 语义 | 证据 |
|---|---|---|---|
| ~149k | string-decoder | 字符串解码（逐字符 FSM，周期×5 ≈ 程序字节数） | 周期相关性 + charCodeAt 签名 |
| ~88k | bit-reader | 程序位流变长读取/解密（`.j4` 缓冲 ⊕ `c6` 密钥流，位掩码累积） | 源码签名 |
| ~67k/34k/9k | cipher | 旋转移位轮函数（`>>>/<<24/^=`）与 XOR 流 push | 源码签名 |
| ~57k | table-permutation | handler 表置换/采样（取模索引 `Y[f](D[h])`） | 源码签名 |
| ~30k | string-accessor | 字符串表访问器（`while(0==![]){return…}` 空转包裹） | 源码签名 |
| ~775 | decompressor | 程序解压器（位流读取器 + `(P(3)+1, P(5))` 表头） | 源码签名 |

产物：`derived/loop-roles.json`（两个 session 均已生成）。

## 二、派发形态裁决：table-indirect（observed）

第二阶段遗留问题「opcode 派发在哪里」的答案：**不存在集中式派发循环**。

三条 observed 证据（四个动态源码一致，`derived/dispatch-verdict.json`）：

1. **handler-table-registrar**：`Du(l,p,c)` 把 handler 按数值 id 写入 `l.Y[p]`；
   140/170 为可追加型（concat），166/282/14/69/283/488/499/412/366/210 走
   `TN(p,43,16,…)` 特殊包装，其余默认 `TN(p,43,3,…)`；id 232 注册时重置密钥流
   `l.W=bC(l,false,32)`。
2. **create-dispatch**：`t(l,p){c=p.Y[l];…c.create()}`——派发经表项 `.create()`
   间接调用，全源码仅 6 个调用点，分散在函数内。
3. **scope-stack**：`TY.push(Y.slice())` 在调用帧切换时压/弹 handler 表。

旁证：全源码最大 switch 仅 31 case（DOM 工具函数）。table-permutation 循环的
角色由此闭环：置换的正是 handler 表 `Y` 的子表，产物经 `Du` 注册回表。

## 三、opcode handler 语义假设（inferred/gap，禁止升级）

| opcode id | 假设 | 等级 |
|---|---|---|
| 232 | 注册时重置密钥流，疑似解密上下文初始化/轮换点 | inferred |
| 140 / 170 | 可追加型 handler，疑似列表/回调收集 | inferred |
| 166 等 10 个 | TN 特殊包装组，疑似需要额外上下文 | gap |

产物：`derived/opcode-hypotheses.json`（`opcode_report.py` 聚合，
纪律：循环角色语义=observed；handler 语义≤inferred）。

## 四、时序关联能力（Task 3，observed）

`vm_loop_trace` 新增 `tick_times`（默认关）：每循环记录相对首 tick 的毫秒
时间戳，与 `states` 平行。原生 script 事件 `w` 字段（epoch µs）与循环
`first_ts`（epoch ms）+ `times` 同一 wall-clock 坐标系。验证：VM 主窗口
134ms 内 14 个原生 script 事件（eval 通道 7 enter + 7 exit 平衡），JS 层
轨迹无未覆盖执行。

## 五、本阶段工具改动

- `hooks/vm_loop_trace.js`：`fsmVarAfter`（for(init;;)/while(true) FSM 变量
  识别，init 赋值校验防误抓）+ `TICK_TIMES` 开关。node 离线 46 项全过。
- `tools/vm_loop.py`：`tick_times` 参数；MCP 110 项全绿。
- `scripts/reverse-browser-google-login-smoke.py`：移除硬编码 state_vars，
  开启 tick_times。
- `adapters/google_botguard/`：新增 `loop_roles.py`（12 测试）、
  `dispatch.py`（6 测试）、`opcode_report.py`（7 测试）。

## 六、已知边界（Task 5 登记）

- Worker realm 只记录不插桩（第二阶段遗留，本阶段未观察到 Worker 使用）；
- 原生 script 事件的 chrome 噪音（resource://）未过滤，分析时需排除；
- `times` 受 MAX_STATES 截断（与 states 同步截断），长循环只覆盖前段；
- 每个 build 的状态数/函数名全变（混淆随机化），分类器按结构签名而非名字。

## 七、测试状态

adapters 42（loop_roles 12 + dispatch 6 + opcode_report 7 + 既有 17）、
MCP 110、node 离线 46——全绿。pythonlib 与 unittest 契约见 Task 5 收尾。
