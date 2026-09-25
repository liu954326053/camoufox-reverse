# 第七阶段计划：taint-lite 值级因果关联

> 前置：第六阶段 wasm 覆盖完成（`docs/reverse8-phase6-wasm-coverage-2026-09-25.md`）。
> 能力模型第 4 节登记的核心缺口：运行链路三段（采集点→变换→落地）各自有记录，
> 因果连接靠人工对。本阶段建 taint-lite：变换层编码/加密 API 的值事件 +
> 离线关联器把请求字段值匹配回产生它的 API 调用。

**Goal:** 浏览器产物能离线回答「这个加密参数值是哪个 API 在哪个 realm
什么时间产出的」。验收：评分卡第 6 条从常驻 unknown 变为可 pass；
靶场端到端链路命中；抖音实测如实登记。

**Architecture:** 值事件采集是通用 hook（hook 9），不认识任何目标；
关联逻辑在探针评分卡（通用，基于值相等匹配），目标特定的参数名猜测
仍属适配器层。

**纪律：** 值事件只记哈希+长度+≤64 字符预览（不全文留存，控制体积与
敏感面）；环形缓冲上限 1000 条/realm；包装不改变返回值/异常语义；
node 离线验证先行。

## Task 1: hook 9 值变换事件（vm_loop_trace.js）✅

- 包装清单（变换层编码/加密 API）：
  `btoa` / `atob` / `encodeURIComponent` / `decodeURIComponent` /
  `TextEncoder.prototype.encode` / `TextDecoder.prototype.decode` /
  `crypto.subtle.digest` / `encrypt` / `sign`
- 事件：`{api, in_len, in_hash, in_preview, out_len, out_hash, out_preview, ts}`
  （哈希用 hashOf 同款 31 滚动哈希，字符串按 UTF-16 码元、字节按字节，
  与 Python 侧 _js_hash / _bytes_hash 对齐）
- 环形缓冲 `valueTaps`（上限 1000/realm），drain() 输出 `value_taps` +
  `value_taps_overflow`
- crypto.subtle.* 异步：Promise 到达后记事件（ts 取完成时）

## Task 2: 聚合与契约 ✅

- `vm_loop.py _drain_log`：value_taps 跨 realm 合并（打 realm 标签、ts 升序）；
  `stop` 回传 route stats（route_hits/errors/content_types）
- node 用例：各 API 事件、预览截断、环形上限、异步 digest、drain 输出、
  异常语义不变（atob 非法输入仍抛不记事件）——94 项 ALL PASS

## Task 3: 探针关联器 + 评分卡第 6 条 ✅

- 候选值提取：URL query + POST request.body（urlencoded/JSON），8–512 字符
- 匹配：哈希对哈希（长值）/ 预览全等或 64 字符前缀（短值）；
  关联表 `{param, api, realm, ts, url}` ≤10 条
- check 6：命中 → pass；有候选无命中 → unknown 附原因；靶场 2 条命中 pass

## Task 4: 靶场 + 实测 + 收尾 ✅

- 靶场：TextEncoder→digest→hex→btoa→XHR POST，check 6 pass，
  链上值连续性完整可读
- 抖音：候选 27 无命中记 unknown（JSVMP 字符串原语链不经标准编码 API，
  如实登记为第八阶段候选：采样式字符串原语包装）
- 附带发现：route.fetch 对 loopback 必然 socket hang up（python/node 服务器
  均复现，真实站点正常）——route 层改写本地靶场不可用，已加 stats 可观测
- 三套回归 + node：121/48/363/94 全绿；报告
  `docs/reverse8-phase7-taint-lite-2026-09-25.md`；git 不主动提交

## 执行顺序

Task 1 → 2 → 3 → 4。
