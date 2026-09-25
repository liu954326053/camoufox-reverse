# 第九阶段：opcode 执行序列 + 参数记录（2026-09-25）

## 背景与目标

能力模型路线图仅剩的 P0：代码补丁通道只有频次计数（`__mcp_vm_counters`），
无顺序、无参数。本阶段补通用序列记录设施，把评分卡第 4 条从
needs-adapter 推进到「有补丁锚点即 pass」。

## 改动清单

### 1. 序列记录设施（vm_loop_trace.js）

- `window.__mcp_vm_rec(tag, vals)`：推入 `{seq, tag, ts, vals}`
  （vals 每项经 metaOf → 哈希+长度+≤64 预览，最多 8 项；可省略）
- `trace_seq` 环形缓冲 5000 条/realm + `trace_seq_overflow` 自描述
- 设计边界（如实）：返回值记录需包裹函数体，正则锚不通用——
  本设施记**入口序列与参数**；返回值由适配器锚 return 语句（如需）

### 2. 聚合（vm_loop.py）

`trace_seq` 跨 realm 合并：realm 标签 + （ts, seq）排序；overflow 求和。

### 3. 探针通道（reverse-browser-vm-target-probe.py）

- 新增 `--code-patches`（JSON 字符串或 @文件），透传 vm_loop_trace install，
  补丁名单记入 summary
- check 4：产物有 trace_seq → pass（条数 + tag 分布）；无 → needs-adapter
  （通道就绪、需锚点，如实）

### 4. node 渲染脚本沉淀（tests/render_runtime.py）

离线契约的渲染命令从临时内联片段固化为脚本，双补丁
（count-register + seq-register）配置集中管理。

## 验证证据

### node 离线契约（100 项 ALL PASS，新增 3 项）

rec 序列有序 + 参数预览 + 省略 vals、环形 5000 + overflow、
补丁端到端（seq-register 锚 Du 入口 → eval 调用 3 次 → 序列 [232,140,232]）。

### 靶场端到端（scripts/fixtures/opcode-target/）

VM 风格 `Du(l,p,c)` 经 eval 进入（动态通道，天然绕开 loopback route 盲区），
程序 `[3,1,4,1,5,9,2,6]`：

- check 4 **pass**：trace_seq 8 条，保序与程序逐字一致，参数预览正确
- eval 的 while(true) FSM 同步注入（1 循环 4 迭代 0 截断）
- 总分 7/10；产物：`artifacts/analysis/phase9/opcode-fixture-20260925/`

### 抖音回归（无补丁，被动 15s）

- 无退化：93/93 源码全文、33 循环 0 截断、33/33 带 times
- **意外之喜**：本轮重载会话（354 请求、1000 值事件）check 6 首次
  **pass**——10 条命中全是 encodeURIComponent 产出的 URL 参数
  （x-expires/sdk_version/spot_keys 等），真实目标上验证了关联机制；
  x-bogus/msToken 仍未命中（VM 内字符串原语，第八阶段已登记的边界）
- 总分 **8/10**（历史最高）；产物：`artifacts/analysis/phase9/douyin-regression-20260925/`

### 回归

MCP 122 passed（新增 trace_seq 跨 realm 合并用例）/ adapters 48 /
pythonlib 363 / node 100 项 ALL PASS。

## 已知限制（如实登记）

1. **锚点正则怕改名**：混淆名再生（瑞数/Kasada）会让适配器补丁失配——
   结构签名锚点仍是路线图 P1。
2. **返回值不在序列里**：入口序列+参数是本设施边界；返回值需适配器
   锚 return 语句另行记录。
3. **环形 5000/realm**：长 VM 会话溢出时 oldest 丢弃（overflow 自描述）。

## 产物索引

- hook：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/hooks/vm_loop_trace.js`（`__mcp_vm_rec` 设施）
- 聚合：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/vm_loop.py`（trace_seq 合并）
- 探针：`scripts/reverse-browser-vm-target-probe.py`（--code-patches + check 4）
- 靶场：`scripts/fixtures/opcode-target/`（index.html + patches.json）
- node 渲染：`integrations/camoufox-reverse-mcp/tests/render_runtime.py`
- node 契约：`integrations/camoufox-reverse-mcp/tests/verify_vm_loop_inject.node.js`（100 项）
