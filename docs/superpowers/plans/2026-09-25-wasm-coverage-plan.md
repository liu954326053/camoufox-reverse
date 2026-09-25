# 第六阶段计划：wasm 盲区覆盖

> 前置：第五阶段能力爬坡完成（`docs/reverse8-phase5-multi-target-2026-09-25.md`），
> 需求池 P0 之首是 wasm：抖音 `pylon-wasm/index_v2.wasm` 实锤加载但内部全盲；
> DataDome 已公开 VM+wasm 三层架构。本阶段让浏览器产出 wasm 证据。

**Goal:** 对 WebAssembly 的四个入口（compile / instantiate /
compileStreaming / instantiateStreaming）全量插桩：模块字节留存（或哈希
交叉命中）、imports/exports 清单、wasm↔JS 边界的双向调用计数。
验收目标：抖音 pylon-wasm。

**Architecture:** wasm 插桩是通用浏览器能力（hook 层），不认识任何目标；
评分卡 wasm 项改为「有 wasm 请求但无模块证据 → fail」的诚实判定。

**纪律：** 包装不得破坏 wasm 语义（instantiate 返回值两种形态、
imports 结构、函数身份）；字节留存设上限（≤768KB 内嵌 base64，
超限留哈希靠 raw 网络捕获交叉）；node 离线验证先行。

## Task 1: wasm hook（vm_loop_trace.js）✅

- `hashBytes`（字节版 31 滚动哈希，与 hashOf 同款便于 Python 交叉）
- compile/instantiate 记录字节哈希+长度，WeakMap 追 module→{字节,条目}
- streaming 变体经 source Promise 旁路取字节（clone，不消耗原响应）
- imports/exports 函数包装计数，写入 `__mcp_vm_counters.wasm_imports /
  wasm_exports`（键 `<hash>:<name>`），随 drain 跨 realm 合并
- drain() 输出 `wasm_modules`（哈希、长度、imports/exports 名单、
  字节 ≤768KB 内嵌 base64）
- 实测补修：streaming exports 回写竞态（entryBox.promise 链）、
  SpiderMonkey 冻结 exports 对象（如实标 exports_frozen，计数降级为名单）、
  instantiate(module) 经 WeakMap 条目链接精确回写 compile 条目

## Task 2: 聚合与契约 ✅

- `tools/vm_loop.py`：wasm_modules 跨 realm 合并（打 realm 标签）
- node 用例：四个入口、两种 instantiate 返回形态、imports/exports 计数、
  字节留存上限（84 项 ALL PASS，wasm 新增 13 项）

## Task 3: 评分卡 + 抖音复测 ✅

- 探针 check 9：wasm 请求与 wasm_modules 证据交叉（哈希命中 raw 网络
  捕获即 pass）；无证据时跑活性自检区分「盲区 fail」与「目标未触发 unknown」
- 抖音复测：pylon-wasm 被动窗口内未实例化（惰性加载），记 unknown 而非 fail，
  hook 活性已验证；本地靶场四入口端到端 pass（交叉命中 3）

## Task 4: 收尾 ✅

- 三套回归 + node：MCP 120、adapters 48、pythonlib 363、node 84 全绿；
  报告 `docs/reverse8-phase6-wasm-coverage-2026-09-25.md`；git 不主动提交

## 执行顺序

Task 1 → 2 → 3 → 4。
