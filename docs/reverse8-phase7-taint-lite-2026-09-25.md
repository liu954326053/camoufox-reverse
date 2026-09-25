# 第七阶段：taint-lite 值级因果关联（2026-09-25）

## 背景与目标

能力模型第 4 节登记的核心缺口：运行链路三段（采集点→变换→落地）各自有记录，
因果连接靠人工对。本阶段建 taint-lite——变换层编码/加密 API 的值事件 +
离线关联器把请求字段值匹配回产生它的 API 调用，评分卡第 6 条从常驻
unknown 变为可 pass。

## 改动清单

### 1. hook 9：值变换事件（vm_loop_trace.js）

包装变换层编码/加密 API，记录 `{api, ts, in/out 的 len+hash+≤64 预览}`：

- `btoa` / `atob` / `encodeURIComponent` / `decodeURIComponent`（单参字符串变换）
- `TextEncoder.prototype.encode` / `TextDecoder.prototype.decode`（字节↔字符串）
- `crypto.subtle.digest` / `encrypt` / `sign`（异步，Promise 完成时记事件）
- 哈希与 hashOf/hashBytes 同款 31 滚动（字符串 UTF-16 码元 / 字节），
  Python 侧 `_js_hash` / `_bytes_hash` 可复算
- 环形缓冲 1000 条/realm，`value_taps_overflow` 计数随 drain 输出；
  包装不改变返回值与异常语义（atob 非法输入仍抛、不记事件）

### 2. 聚合（vm_loop.py）

`_drain_log` 跨 realm 合并 `value_taps`（打 realm 标签，按 ts 升序）；
`stop` 顺带回传 route stats（本次排查刚需，通用可观测性改进）。

### 3. 探针关联器（reverse-browser-vm-target-probe.py check 6）

- 候选值提取：请求 URL query + POST `request.body`（urlencoded/JSON），
  长度 8–512 的字符串值
- 匹配：候选值哈希 == tap `out_hash` 且长度相等（长值）；或与 `out_preview`
  全等/64 字符前缀（短值/截断）
- verdict：命中 → pass 附关联表 `{param, api, realm, ts, url}`（≤10 条）；
  有候选无命中 → unknown 并说明原因（未走包装清单 API / 环形溢出）

## 验证证据

### node 离线契约（94 项 ALL PASS，新增 10 项值事件用例）

各 API 事件记录、哈希与 node 侧复算一致、hex 预览、异步 digest、
预览截断（in_len 100 / preview 64）、atob 异常语义保持且不记事件、
环形上限 1000 + overflow 计数。

### 靶场端到端（scripts/fixtures/taint-target/）

链路 `raw → TextEncoder → digest(SHA-256) → hex → btoa → XHR POST 字段`：

- 值事件 6 条，链上值连续性完整可见（digest 输出 hex == btoa 输入预览逐字一致）
- check 6 **pass**：`sig`（88 字符，哈希匹配）、`uid`（8 字符，短值匹配）
  两条关联命中，含 api/realm/ts
- 产物：`artifacts/analysis/phase7/taint-fixture-20260925c/`

### 抖音实测（被动加载 15s）

- check 6 **unknown**（诚实）：候选 27 个无命中（值事件 20 条）——
  x-bogus/msToken 在 JSVMP 循环内用字符串原语（charCodeAt/拼接）计算，
  不经标准编码 API；这是已知边界，不是误判
- 总分 7/10（本次无 wasm 请求，check 9 pass）
- 产物：`artifacts/analysis/phase7/douyin-taint-20260925/`

### 回归

MCP 121 passed / adapters 48 / pythonlib 363 / node 94 项 ALL PASS。

## 排查插曲：route 改写的 loopback 盲区（重要附带发现）

靶场 0 循环引出深挖：route stats（本次新增可观测）显示 **route.fetch 对
loopback 地址（127.0.0.1 与 localhost）必然 socket hang up**——python 与
node 服务器都复现，真实站点（抖音 53 命中 0 错误、6 个外链 JS 改写 32 循环）
完全正常。结论：camoufox 的 Playwright 构建对回环地址的 route.fetch 有兼容问题，
**route 层改写（内联 HTML/外链 JS 插桩）在 loopback 靶场不可用**；
运行时 hook（eval/Function/值事件/wasm）不受影响。

影响与处置：

- 本地靶场的 route 路径验证改用离线单测覆盖（rewrite_inline_scripts /
  rewrite_js_body 已有 pytest）；真实目标实测不受影响
- route handler 现在统计 `route_hits / route_errors / last_error /
  content_types`，`stop` 回传——此类静默失败以后第一行数据就能看到

## 已知限制（如实登记）

1. **字符串原语链不在覆盖内**：`charCodeAt` / 手工拼接 / 位运算构造的值
   （抖音 JSVMP 签名正是此类）不产生值事件。包装 String 原型方法热度过高，
   需要采样策略设计，留第八阶段候选。
2. **字节→hex/base64 的手工转换断链**：digest 输出经未包装 JS 转 hex 后，
   hex 值本身无事件；链的连续性靠相邻事件预览拼接可读（靶场实测可读）。
3. **loopback route.fetch 盲区**：见上。
4. 环形缓冲溢出时 oldest 事件丢弃（有 overflow 计数自描述）。

## 产物索引

- hook：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/hooks/vm_loop_trace.js`（hook 9）
- 聚合：`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/vm_loop.py`（value_taps 合并 + stop stats）
- 探针：`scripts/reverse-browser-vm-target-probe.py`（check 6 关联器）
- 靶场：`scripts/fixtures/taint-target/`；HTTP/1.1 服务器 `scripts/fixtures/serve.py`
- node 契约：`integrations/camoufox-reverse-mcp/tests/verify_vm_loop_inject.node.js`（94 项）
