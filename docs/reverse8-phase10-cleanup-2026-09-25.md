# 第十阶段：清尾——剩余 P1/P2 与三条边界（2026-09-25）

目标清单（用户确认）：P1 结构签名锚点、P2 跨域 iframe/ServiceWorker 覆盖、
P2 route.fetch loopback 盲区、P2 插桩分叉检测闭环、P2 流式落盘，
外加路线图外三条边界（wasm exports 调用计数、逐字符装配链关联、惰性 wasm 触发）。

## 切片 A：结构签名锚点（P1，改名免疫补丁）✅

**问题**：第四阶段的通用代码补丁是字面量正则，混淆器一改函数名/变量名
锚点就失效（Google VM 每轮发版都改名）。

**方案**：补丁对象新增可选 `"structural": true`。匹配前对源码做**等长归一化**：
非关键字、非属性访问（前面不是 `.`）的标识符逐字符替换为等长 `_` 串，
字符串/注释/正则字面量跳过，位置 1:1 不变。pattern 针对归一化形态书写
（标识符写 `_+`，关键字保持字面量，空白用 `\s*`）；replacement 里
`$1..$9` 借助 RegExp `/d` 指数的捕获组 span **切回原源码恢复真名**。

**效果与限制**：
- 局部变量/函数改名免疫（混淆器最常见手法）；
- 属性改名仍会失配（如实限制，属性语义恰好是分析锚点时常反而要保真）；
- 引擎需支持 RegExp `/d`（SpiderMonkey ≥ Firefox 88、Node ≥ 16），不支持则该条补丁跳过；
- 字面量补丁不跳过字符串（第四阶段已有行为），structural 补丁的归一化跳过字符串。

**实现**：`vm_loop_trace.js` 补丁通道（`normalizeIdentifiers` +
`applyStructuralPatch`）；Python 侧 `code_patches` JSON 透传零改动；
`vm_loop` 工具 docstring 补充 structural 字段说明。

**验证**：
- node 契约 +5 用例（改名后仅 structural 命中、真名恢复、关键字/属性
  不归一化、字符串不被归一化破坏、端到端执行记录参数），全套 105 项 ALL PASS；
- 三套 Python 回归全绿（MCP 122+2s / adapters 48 / pythonlib 363+5s）；
- 实战靶场 `scripts/fixtures/opcode-target-renamed/`（opcode-target 的改名版，
  Du→Xq、l/p/c→aa/bb/cc）：structural 锚点命中，trace_seq 完整还原
  opcode 序列 [3,1,4,1,5,9,2,6]，tag 恢复为真实函数名 `Xq`，
  `patches_applied: ["seq-register-structural"]` 落盘。

## 切片 B：route.fetch loopback 绕行（P2）✅

**问题**：Playwright `route.fetch()` 对 loopback（127.0.0.1/localhost/::1）
必 socket hang up（python/node 服务器均复现，真实站点正常），导致本地
靶场的 HTML/JS 响应完全走不到改写管线，route_errors 计数、零产出。

**方案**：`vm_loop.py` route handler 拆成「取响应」与「改写管线」两段；
取响应异常时若 URL 是 loopback，用 urllib 在线程里按原方法/头/body
重取（`asyncio.to_thread`），再走同一改写管线 fulfill。stats 新增
`route_fetch_fallback` 计数。探针收尾补 `vm_loop_trace stop` 调用并把
route 统计写入 summary（此前统计被直接丢弃，绕行是否生效不可观测）。

**验证**：
- taint 靶场（内联 `while(true)` 循环）：`route_fetch_fallback: 2`、
  `route_errors: 0`、`documents_rewritten: 1`、`loops: 1`，内联循环
  真实 tick 6 次落盘——此前 loopback 靶场内联改写全灭，现在全通；
- opcode 靶场：fallback 生效、HTML 管线正常（该靶场循环在 eval 通道，
  route 层 0 循环属预期）；
- MCP 回归 122 passed, 2 skipped 无回归。

## 切片 C：wasm exports 计数 + fromCharCode 合并（边界 1/2）✅

**C1 wasm exports 调用计数（边界 1）**：实测 SpiderMonkey——`exports`
是原型 getter（configurable），exports 对象冻结，instance 可扩展；
直接赋值静默失败；Proxy get 对冻结属性违反不变量抛 TypeError。
**可行方案：浅拷贝包装对象 + defineProperty 在 instance 上遮蔽原型
getter**。`wrapWasmExports` 改为统一走遮蔽路径，条目保留
`exports_frozen: true` 如实标记，遮蔽失败记 `exports_shadow_failed`。
真浏览器 wasm 靶场验证：三个实例化形态的 `run()` 调用全部计数
（`59143cee:run: 3`）——此前 SpiderMonkey 下 exports 计数为零。
残余盲区：`new WebAssembly.Instance()` 构造器直连路径未 hook
（四入口之外的第五入口，使用率低，如实登记）。

**C2 逐字符装配链合并（边界 2）**：hook 10 加合并缓冲——单字符输出
追加进 `sfcPending`；触发 ≥2 字符调用 / 缓冲满 64 字符 / drain 前
flush 为一条 `String.fromCharCode(coalesced)` 事件。
`string_primitives` 增加 `fromCharCode.coalesced` 计数。
限制：并行装配的多条链并入同一缓冲无法拆链，但 64 字符粒度下
片段匹配可用。
抖音实测：339 次单字符调用合并为 6 条事件，片段含
`bytedanceUIFID…byte_sign…_sign…MD5AESdecrypt` 等签名相关标识符，
分析原料从「只有计数」变为「可读装配链」。本轮 check 6 仍 unknown
属负载波动（被动会话只 7 条值事件、未触发签名段），非能力缺口。

**验证**：node 契约 +6 用例（冻结遮蔽计数、frozen 标记、合并事件
内容/64 字符 flush/drain 残余、环形溢出口径更新），全套 ALL PASS。

## 切片 D：跨域 iframe drain 桥 + ServiceWorker 盲区登记（P2）✅

**跨域 iframe（P2）**：init script 本就覆盖所有 frame（含跨域），缺口
只在 drain——父页面无法同步访问跨域 frame 的 realm 状态。补双向
postMessage 桥：
- 应答侧：每个 window realm 注册 message 监听，仅响应
  `e.source === window.parent` 的 `{__mcp_vm_drain_req}` 请求（防页面
  伪造请求套取轨迹），回传本 realm `drainAllAsync` 结果（含本 frame
  的 Worker 与嵌套 frame，递归聚合）；
- 请求侧：`drainAllAsync` 对同步访问抛错的 frame 发请求并带超时，
  结果加 `frame[i]/` 前缀；桥成功的 frame 清掉 drainAll 残留的同步
  访问错误条目；无应答如实记 `cross-origin drain timeout`。

**ServiceWorker（P2）**：init script 覆盖不到 SW realm（独立生命周期），
包装 `ServiceWorkerContainer.prototype.register` 如实记
`serviceworker-gap` 事件（与 worker-gap 同通道），评分卡 check 9
纳入计数。

**顺带修复**：探针 check 9 一直读 `summary.iframe_census` 的包壳
（`{"type":"json","value":[...]}`）导致 census 恒为空、跨域永远不计
——解包修复；跨域 frame 已经桥聚合的不算缺口，未覆盖才算 fail。

**验证**：
- node 契约 +4 用例（跨域桥聚合 6 次 tick、残留错误条目清除、无应答
  超时、非父窗口伪造请求被忽略、SW gap 登记且原样直通），全套 ALL PASS；
- 真浏览器跨域靶场 `scripts/fixtures/xorigin-frame-target/`（双端口
  跨源）：realms 聚合出 `frame[0]/top`，子 frame 的 FSM 循环与
  fromCharCode 装配循环轨迹完整回传；check 9 如实报
  「跨域 1：已桥聚合 1、未覆盖 0」；
- MCP 回归 122 passed 无回归。

## 切片 E：流式落盘 + 插桩分叉检测闭环（P2 ×2）✅

**E1 流式落盘**：`_drain_log` 写 artifact 时，payload 超 32MB 把
`loops[].states` 拆到旁车 `raw/vm-loop/<id>-states.ndjson`（每行
`{loop, realm, i, state}`），主 JSON 留元数据 + `states_file` 指针 +
`states_streamed` 自描述；未超阈值保持单文件形态。
`adapters/google_botguard/divergence.py` 的 `browser_state_sequence`
同步兼容：跟着 `states_file` 指针读旁车，序列结论与内联形态一致。
新增 3 个单元测试（拆分形态/小 payload 不拆/旁车指针回溯）。

**E2 插桩分叉检测闭环**：能力模型 P2 的「插桩分叉」指**插桩是否改变
目标行为**（强反调试目标如 Kasada 的核心验收），与
`divergence.py` 的浏览器↔外部复现对比是两回事。新增系统化脚本
`scripts/reverse-browser-instrumentation-divergence.py`：同一目标跑
被动/插桩两会话，比较请求 (method, host+path) 多重集、响应状态分布、
加密参数签名在位率、最终页面 URL，裁决 aligned/minor/diverged/gap
并附差异证据，写 `instrumentation-divergence.json`。
本地靶场实测：aligned（请求多重集完全一致），插桩侧
`route_fetch_fallback: 1` 正常（loopback 绕行顺带复验）。

**验证**：MCP 34 项 vm_loop 测试全绿（含 2 新增）、adapters 49 全绿
（含 1 新增）、分叉脚本本地靶场端到端 aligned。

## 切片 F：总回归 + 文档同步 ✅

- node 契约：ALL PASS（累计 118 项，含第十阶段新增 15 项）；
- MCP 124 passed, 2 skipped；adapters 49 passed；pythonlib 363 passed, 5 skipped；
- 抖音全量复测 7/10 与第八/九阶段基线持平（needs-adapter ×2 为适配器
  职责；check 6 unknown 为被动会话未触发签名段的负载波动，非回归；
  首跑 3 请求的样本已识别为空会话波动并复跑确认）；
- 能力模型矩阵/路线图/三边界已同步（vm-reverse-capability-model-2026-09-25.md）。

## 边界 3 结论（惰性 wasm 触发）

判定为**目标侧行为**而非浏览器缺口：目标在交互后才实例化 wasm 是目标
逻辑，浏览器侧的 `--click-expression` 触发 + wasm 四入口证据链即可闭
环，无需浏览器改动。

## 第十阶段总账

| 项 | 状态 | 证据 |
|---|---|---|
| P1 结构签名锚点 | ✅ | 改名靶场序列完整还原，tag 恢复真名 |
| P2 跨域 iframe / ServiceWorker | ✅ / 🟡（SW 引擎边界如实登记） | 跨域靶场 frame[0]/top 聚合 |
| P2 route.fetch loopback | ✅ | taint 靶场内联改写全通，fallback 可观测 |
| P2 插桩分叉检测 | ✅ | 双会话对比脚本，本地靶场 aligned |
| P2 流式落盘 | ✅ | >32MB 拆旁车 NDJSON，divergence.py 兼容 |
| 边界 1 wasm exports 计数 | ✅ | 冻结 exports 遮蔽计数，真浏览器 3 形态验证 |
| 边界 2 逐字符装配链 | ✅ | 合并缓冲，抖音实测可读装配链 |
| 边界 3 惰性 wasm 触发 | ✅（非浏览器缺口） | --click-expression 闭环 |
