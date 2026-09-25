# 第九阶段计划：opcode 执行序列 + 参数记录

> 前置：能力模型路线图仅剩的 P0。现状：代码补丁通道只有频次计数
> （__mcp_vm_counters），无顺序、无参数。本阶段补通用序列记录设施，
> 把评分卡第 4 条从 needs-adapter 推进到「有补丁锚点即可 pass」。

**Goal:** 运行时提供 `__mcp_vm_rec(tag, values)` 序列记录设施——
补丁（目标适配器提供锚点正则）在 handler 入口插入调用，产物即含
**有序** 的 handler 触发序列 + 参数证据（哈希/长度/预览）。
验收：靶场端到端序列保序 + 参数命中；抖音回归无退化。

**Architecture:** 设施是通用 runtime 能力；锚点正则仍是目标适配器职责。
返回值记录需要包裹函数体，正则锚做不到通用——本阶段记入口序列与参数，
返回值由适配器锚 return 语句（如有需要），如实写进文档。

**纪律：** 环形缓冲 5000 条/realm + overflow 自描述；复用 metaOf
（哈希+≤64 预览，不全文留存）；rec 自身开销最小化（热路径）；
node 离线验证先行。

## Task 1: 序列记录设施（vm_loop_trace.js） ✅

- `traceSeq` 环形缓冲（5000/realm）+ `traceSeqOverflow`
- `window.__mcp_vm_rec = function(tag, vals)`：推入
  `{seq, tag, ts, vals: [metaOf(v)...]}`（vals 可省略）
- drain() 输出 `trace_seq` + `trace_seq_overflow`

## Task 2: 聚合与契约 ✅

- `vm_loop.py _drain_log`：trace_seq 跨 realm 合并（realm 标签 + ts 排序）
- node 用例：rec 记录/省略 vals/环形上限/overflow/drain 输出/
  补丁调用 rec 端到端（regex patch → Du 入口 → 序列有序）

## Task 3: 探针通道打通 ✅

- 探针加 `--code-patches`（JSON 字符串或 @文件），透传 vm_loop_trace install
- 评分卡 check 4：产物有 trace_seq → pass（附条数与 tag 分布）；
  无 → needs-adapter（原料齐、需锚点）如实不变

## Task 4: 靶场 + 回归 + 文档 ✅

- 靶场新页：VM 风格 `Du(l,p,c)` 派发函数在循环里跑 opcode 序列，
  探针带补丁验收序列保序与参数预览
- 抖音回归（无补丁，确认无退化）
- 三套回归 + node；报告 `docs/reverse8-phase9-opcode-seq-2026-09-25.md`；
  能力模型矩阵/路线图同步；计划勾选；git 不主动提交

## 执行顺序

Task 1 → 2 → 3 → 4。

## 完成备注（2026-09-25）

- __mcp_vm_rec 设施 + trace_seq 跨 realm 合并；node 100 项 ALL PASS
- 靶场 8 条序列与程序 [3,1,4,1,5,9,2,6] 逐字保序，check 4 pass
- 抖音回归无退化且本轮重载会话 check 6 首次 pass（10 条
  encodeURIComponent 命中），总分 8/10 历史最高
- 回归：122/48/363/100 全绿；报告 docs/reverse8-phase9-opcode-seq-2026-09-25.md；
  能力模型矩阵/路线图已同步
