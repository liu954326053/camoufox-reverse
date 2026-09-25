# 第八阶段计划：字符串原语取证 + 状态快照关联

> 前置：第七阶段 taint-lite 完成（`docs/reverse8-phase7-taint-lite-2026-09-25.md`）。
> 抖音实测的诚实边界：JSVMP 签名用 charCodeAt/拼接/fromCharCode 等字符串
> 原语计算，不经标准编码 API，check 6 记 unknown。本阶段补这一段。

**Goal:** 让「VM 内部字符串装配 → 请求字段」这一段也能关联。
两条互补路径：
1. hook 10：`String.fromCharCode` 取证——混淆 VM 拼装字符串的典型末段；
   单字符调用是热噪声只计数，≥2 字符输出记事件。
2. 关联器扩展：候选值除了匹配值事件，还匹配 **loop 状态快照里的字符串**
   （已有产物，增量为零）与 fromCharCode 片段（子串包含）。

**Architecture:** 均为通用能力，不认识目标；fromCharCode 全量计数入
counters，事件进 valueTaps 环（≥2 字符）；状态快照扫描在离线关联器。

**纪律：** 单字符调用绝不记事件（331k 迭代量级会撑爆环形缓冲）；
状态扫描先做字符串去重再匹配，控制离线耗时；node 离线验证先行。

## Task 1: hook 10（vm_loop_trace.js） ✅

- `String.fromCharCode` 包装：返回值原样透传；
  全量调用计数入 `counters.string_primitives['fromCharCode.calls']`；
  输出 ≥2 字符记 valueTaps 事件（api=String.fromCharCode）

## Task 2: 聚合与契约 ✅

- 无需改聚合（复用 value_taps / counters 通道）
- node 用例：多字符事件记录、单字符只计数、计数准确、返回值不变

## Task 3: 关联器扩展（probe check 6） ✅

- 证据集 = 值事件 out（哈希/预览）+ fromCharCode 片段 + loop 状态快照
  字符串（去重后）
- 匹配等级：tap-hash（精确）> tap-preview（前缀/全等）> state-exact
  （快照全等）> fragment（≥4 字符片段被候选值包含）
- linkage 条目带 `via` 与来源定位（api 或 loop id + realm + ts）

## Task 4: 靶场 + 实测 + 收尾 ✅

- 靶场新页：fromCharCode 片段装配签名 → XHR POST，验证 fragment 命中；
  单字符装配链只计数不记事件的边界如实展示
- 抖音复测：x-bogus/msToken 候选与扩展证据集的匹配结果如实登记
- 三套回归 + node；报告 `docs/reverse8-phase8-string-taint-2026-09-25.md`；
  计划勾选；git 不主动提交

## 执行顺序

Task 1 → 2 → 3 → 4。

## 完成备注（2026-09-25）

- hook 10 计数改独立字段 `string_primitives`（不进 __mcp_vm_counters——
  补丁自初始化惯用法兼容约束，node 契约实测暴露）
- hook 8 bytesToB64 自污染修复（捕获原始 fromCharCode）
- 靶场 check 6 pass（fragment 前缀命中）；抖音 unknown（轻载窗口，如实）
- 回归：121/48/363/97 全绿；报告 docs/reverse8-phase8-string-taint-2026-09-25.md
