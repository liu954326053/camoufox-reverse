# 第六阶段：WebAssembly 盲区覆盖（2026-09-25）

## 背景与目标

第五阶段抖音实测的诚实扣分项：目标加载了 `pylon-wasm`（1.17MB），但 wasm 模块字节、
imports/exports、wasm↔JS 边界调用全部不可见。本阶段把 wasm 证据采集补进浏览器核心能力，
并修订评分卡第 9 条为诚实判定。

## 改动清单

### 1. hook 8：WebAssembly 四入口插桩（vm_loop_trace.js）

对 `compile / instantiate / compileStreaming / instantiateStreaming` 四入口插桩：

- **模块字节证据**：字节版 31 滚动哈希（与 hook 的 `hashOf` 同款，便于 Python 交叉）；
  ≤768KB 内嵌 base64 全文（分块 btoa）；超限留哈希，靠 raw 网络捕获交叉命中取回字节。
- **module→字节/条目链接**：WeakMap 存 `{bytes, entry}`，`instantiate(module)` 形态
  精确回写 compile 条目的 exports（不再靠「同哈希最新条目」猜测，并发安全）。
- **imports 计数**：包装 imports 对象里的函数进 `__mcp_vm_counters.wasm_imports`，
  键 `<hash>:<module>.<name>`。
- **exports 取证**：记录导出名清单；SpiderMonkey **冻结 exports 对象**
  （`Object.isFrozen(exports) === true`，实测确认），属性替换计数不可用，
  如实标 `exports_frozen: true`。exports 调用计数为已知限制（见「已知限制」）。
- **streaming 竞态修复**：`instantiateStreaming` 的字节旁路（`resp.clone().arrayBuffer()`）
  与原生实例化完成存在竞态——exports 回写现在等旁路字节落账后执行
  （`entryBox.promise` 链），旁路失败不阻塞页面语义。
- **provId 机制**：streaming 形态 imports 必须在实例化前包装（哈希异步才出），
  先以临时 id `s<N>` 包装，字节到达后翻转 idBox 并迁移已积累计数键为 `<hash>:` 前缀。
- **语义保持**：`instantiate(bytes)` 返回 `{module, instance}`、
  `instantiate(module)` 返回 `Instance`，两种形态原样透传。

### 2. drain 聚合（vm_loop.py）

- 运行时 drain() 输出 `wasm_modules`（空则省略）。
- `_drain_log` 跨 realm 合并 `wasm_modules`：每项打 `realm` 标签（含嵌套 Worker 的
  `worker[i]/worker[j]/top` 路径），汇总进 data 与返回值。

### 3. 评分卡第 9 条诚实判定（reverse-browser-vm-target-probe.py）

- 目标有 wasm 请求时：drain 产物有 `wasm_modules` 证据 → pass；再把 drain 哈希与
  `raw/network/*/response.body` 字节做同款 31 滚动哈希**交叉命中**（命中即离线可用）。
- 无证据时跑 **wasm hook 活性自检**：主世界手动实例化最小合法模块（8 字节
  magic+version），二次 drain 验证证据落账——区分「浏览器盲区」（fail）与
  「目标在观测窗口内未实例化」（unknown）。

### 4. 诊断工具（reverse-browser-wasm-diag.py）

现场诊断脚本：四入口包装状态、手动实例化活性验证（空模块 + 带导出模块 +
冻结检测）、iframe realm 普查、drain 证据 dump。

## 验证证据

### node 离线契约（84 项 ALL PASS，新增 13 项 wasm 用例）

假 WebAssembly 件覆盖：两形态返回语义、imports/exports 包装计数、streaming clone、
provId 迁移、base64 还原、四入口 kind 齐全、drain 输出。

### 本地靶场端到端（scripts/fixtures/wasm-target/）

36 字节最小导出模块 `(func (export "run") (result i32) i32.const 7)`，页面四入口全用：

| 条目 | exports | module_compiled | prov_id | exports_frozen |
| --- | --- | --- | --- | --- |
| instantiate(bytes) | `["run"]` | — | — | true |
| compile + instantiate(module) | `["run"]`（回写 compile 条目） | true | — | true |
| instantiateStreaming | `["run"]` | — | s1 | true |
| compileStreaming | —（不产实例，符合预期） | — | — | — |

评分卡 check 9：**pass**（wasm 请求 3，证据 4 条，raw 网络交叉命中 3）。
产物：`artifacts/analysis/phase6/wasm-fixture-20260925c/`

> 插曲：靶场第一版 wasm 字节手写错误（type 段长度 04 应为 05），页面实例化全部
> reject——恰好暴露了「exports 空 + counters 空」的症状差异，驱动了真实浏览器的
> 逐路径诊断（诊断脚本即由此产出）。

### 抖音复测（被动加载 15s）

- check 9：**unknown**（诚实）——pylon-wasm 已加载（raw 捕获 1.17MB 响应体），
  但观测窗口内目标**未调用任何 WebAssembly 入口**（惰性/条件加载）；
  hook 活性已验证（手动实例化证据落账）。
- 其余：33 循环 0 截断 331313 迭代、33/33 带 times、动态源码 93/93 全文、
  native 15376 事件 0 丢失。总分 6/10（unknown 不计分，诚实）。
- 产物：`artifacts/analysis/phase6/douyin-wasm-20260925c/`

### 回归

- MCP：120 passed, 2 skipped（新增 wasm_modules 跨 realm 聚合用例）
- adapters：48 passed
- pythonlib：363 passed, 5 skipped
- node 契约：84 项 ALL PASS

## 已知限制（如实登记）

1. **exports 调用计数不可用**：SpiderMonkey 冻结 exports 对象，属性替换式计数失败。
   当前产物：导出名清单 + `exports_frozen: true`。若需调用计数，后续可评估
   Proxy 包装 instance 返回值（改变对象身份，有语义可观测性风险，需目标实测权衡）。
2. **wasm 内部分派不可见**：wasm 模块内部的 opcode/函数调用属于 wasm 层逆向，
   浏览器侧提供字节 + 边界证据，内部静态分析由分析者用 wasm 工具链完成
   （字节已在产物/网络捕获里，哈希可交叉）。
3. **惰性 wasm 触发**：目标在被动观测窗口内不实例化 wasm 时（如抖音），
   评分卡记 unknown 而非 fail；触发路径（如交互动作）属目标特定适配器范畴。

## 产物索引

- hook：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/hooks/vm_loop_trace.js`（hook 8）
- 聚合：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/vm_loop.py`（`_drain_log`）
- 探针：`scripts/reverse-browser-vm-target-probe.py`（check 9 诚实判定 + 活性自检）
- 诊断：`scripts/reverse-browser-wasm-diag.py`
- 靶场：`scripts/fixtures/wasm-target/`（index.html + test.wasm）
- node 契约：`integrations/camoufox-reverse-mcp/tests/verify_vm_loop_inject.node.js`（84 项）
