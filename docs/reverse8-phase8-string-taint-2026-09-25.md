# 第八阶段：字符串原语取证 + 状态快照关联（2026-09-25）

## 背景与目标

第七阶段抖音实测的诚实边界：JSVMP 签名用 charCodeAt/拼接/fromCharCode
等字符串原语计算，不经标准编码 API，check 6 记 unknown。本阶段补这一段，
两条互补路径：fromCharCode 取证（hook 10）+ 关联器扩展到状态快照与片段匹配。

## 改动清单

### 1. hook 10：String.fromCharCode 取证（vm_loop_trace.js）

- 全量调用计数 → drain 独立字段 `string_primitives['fromCharCode.calls']`
  （**不进 `__mcp_vm_counters`**：补丁的自初始化惯用法 `obj=obj||{key:{}}`
  假定整对象不存在，预建会炸掉补丁——node 契约实测暴露）
- ≥2 字符输出记 valueTaps 事件（VM 拼装签名的典型末段片段）；
  单字符调用是热噪声（331k 迭代量级），只计数不记事件
- 返回值原样透传

### 2. hook 8 自污染修复（同文件）

bytesToB64 原来走公共 `String.fromCharCode`——hook 10 上线后每次 wasm
字节转码都会自产值事件（node 契约实测暴露，s9 的 wasm base64 事件混进
s10 的值事件断言）。改为捕获原始引用 `_fromCharCode0` 专供内部转码。

### 3. 关联器扩展（probe check 6）

证据集三级：值事件 out（哈希/预览）→ loop 状态快照字符串（去重索引，
每循环扫 2 万条、全局 2 万条上限控离线耗时）→ 片段包含匹配。

匹配等级（linkage 带 `via` 字段）：

| via | 语义 |
|---|---|
| tap-hash | 哈希+长度精确命中值事件输出 |
| tap-preview | 短值全等 / 长值 64 字符前缀 |
| state-exact | 候选值全等命中某循环的状态快照字符串（带 loop+realm 定位） |
| fragment / state-fragment | ≥4 字符片段与候选值互相包含 |

### 4. 聚合（vm_loop.py）

`string_primitives` 跨 realm 求和进 drain 产物。

## 验证证据

### node 离线契约（97 项 ALL PASS，新增 3 项）

fromCharCode 多字符事件记录（哈希一致）、单字符只计数不记事件、
返回值原样；环形/截断等既有断言在 hook 10 上线后仍全绿
（补丁计数器冲突与自污染两个回归均被契约拦住并修复）。

### 靶场端到端（scripts/fixtures/string-target/）

`fromCharCode 片段 + btoa 拼接 → XHR POST`：

- check 6 **pass**：`sig-v2` 片段前缀命中（via=tap-preview，
  api=String.fromCharCode，realm/ts 齐全）
- 单字符逐字装配（"X-bogus"）只计数不记事件——边界行为如实展示
- 产物：`artifacts/analysis/phase8/string-fixture-20260925/`

### 抖音实测（被动加载 15s）

- check 6 **unknown**（诚实）：本轮页面加载很轻（54 请求、3 循环、
  61 迭代、0 值事件、0 快照字符串），候选 13 个无命中
- 总分 7/10；产物：`artifacts/analysis/phase8/douyin-string-20260925/`
- 跨轮观察：抖音被动会话的负载深度波动大（364→54 请求），
  check 6 能否命中取决于 JSVMP 签名路径是否在观测窗口内真实运行——
  这本身是被动观测纪律的固有边界，如实登记

### 回归

MCP 121 passed / adapters 48 / pythonlib 363 / node 97 项 ALL PASS。

## 已知限制（如实登记）

1. **逐字符装配链不可关联**：`s += String.fromCharCode(x)` 逐字符拼装的
   中间态不可见（单字符不记事件是体积纪律），最终值若无 ≥2 字符片段
   经包装 API 或快照落盘，仍无法关联。后续若需要，方向是快照侧增强
   （字符串型寄存器已是快照对象）而非放开单字符事件。
2. **快照匹配是相关性而非因果性**：state-exact 证明「值经过这个循环」，
   不证明哪一步产生；精确因果仍需目标适配器的代码补丁钉 handler。
3. 快照字符串索引有 2 万上限，超出部分不参与片段匹配（大 VM 会话注意）。

## 产物索引

- hook：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/hooks/vm_loop_trace.js`（hook 10 + hook 8 自污染修复）
- 聚合：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/vm_loop.py`（string_primitives 合并）
- 探针：`scripts/reverse-browser-vm-target-probe.py`（check 6 三级证据匹配）
- 靶场：`scripts/fixtures/string-target/`
- node 契约：`integrations/camoufox-reverse-mcp/tests/verify_vm_loop_inject.node.js`（97 项）
