# 第十三阶段收口：302 链分歧裁决 + divergence 归一化修复（2026-09-26）

承接 `reverse8-phase13-p2-probes-2026-09-26.md` 的缺口 3（live.com 302 链未遍历）
与缺口 1（归一化不覆盖子域 token）。产物在 `artifacts/analysis/phase13-repro/`。

## 任务 1：signup.live.com 302 document 链分歧 —— 裁决：插桩副作用（route 层吞重定向），已修复

### 复现（插桩/干净各 2 轮，socks5://127.0.0.1:7890，被动纪律）

复现脚本：`artifacts/analysis/phase13-repro/repro_302_chain.py`；
报告：`live302-chain-repro.json`、`live302-chain-repro-clean-r2-instrumented-r1.json`、
`live302-chain-repro-instrumented-r1.json`、`live302-chain-repro-instrumented-fixed-r1.json`。

| 会话 | 终点页面 URL | document 链首跳状态 | 链是否遍历 |
|---|---|---|---|
| clean-r1 | `signup.live.com/?lic=1` ✅ | 302 → Location: login.live.com/login.srf | ✅ 完整（302→login.srf→302→?lic=1→200） |
| clean-r2 | `signup.live.com/?lic=1` ✅ | 302 → Location: login.srf | ✅ 完整 |
| instrumented-r1 | `signup.live.com/`（无 ?lic=1）❌ | **直接 200**（无 302 跳） | ❌ 链消失 |
| instrumented-r2 | `signup.live.com/`（无 ?lic=1）❌ | **直接 200**（无 302 跳） | ❌ 链消失 |

决定性证据：插桩侧首个 document 请求（`GET signup.live.com/`）记录的状态是
**200 而非 302**，且全程无 login.srf 的 document 请求——干净侧同一请求是
302 + Location 头。目标侧 A/B/时序无法解释「302 变 200」；2/2 轮稳定复现。

### 根因

`integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/vm_loop.py`
的 route_handler 对所有请求 `route.fetch()` —— Playwright 的 fetch **默认跟随
重定向**。对 document 导航请求，302 链每一跳被 route 层在内部消费，最终 200
响应被 fulfill 回原 URL：浏览器看不到任何一跳 302，地址栏停在无 `?lic=1`
的 URL，下游 `risk/initialize` OPTIONS 预检（由 ?lic=1 页面触发）随之缺失。
phase13 报告里的两个疑点（302 链未遍历 + 预检缺失）是同一根因。

### 修复（低成本，已落地）

- document 请求（`resource_type == "document"`）的 `route.fetch()` 改为
  `max_redirects=0`，不再内部跟随；
- 新增判据 `_redirect_passthrough(is_document, status, headers)`：
  document + 3xx + Location 的响应**原样 fulfill** 回浏览器（Location 头
  经 `_clean_response_headers` 保留），浏览器自行发出下一跳 document 请求
  并再次命中 route，链上每跳各自走改写管线；
- 非 document 请求（子资源/XHR）保持旧行为（fetch 内部跟随）；
- route_stats 新增 `redirects_passthrough` 计数。

### 修复后实测验证（instrumented-fixed-r1）

- 终点页面 URL = `signup.live.com/?lic=1` ✅，与干净侧一致；
- document 链：`signup.live.com/` → `login.srf`（302 → Location: ?lic=1）→
  `signup.live.com/?lic=1`（200）✅；
- `redirects_passthrough=1`（login.srf 跳；首跳 302 带 text/html 小 body，
  走 HTML 分支 fulfill 时状态与 Location 头同样原样保留，效果等价）；
- 插桩功能无损：route_hits=22，loops=132，js_rewritten=5，route_errors=0。

注：复现期间 MCP 服务器进程间歇性启动失败（"MCP request could not be
written" / "server closed stdout"，约半数首轮尝试），重试后均成功；与本次
裁决无关，但值得另行登记为 MCP 启动稳定性问题。

## 任务 2：divergence 归一化覆盖子域 token —— 已修复并验证

### 改动（`scripts/reverse-browser-instrumentation-divergence.py`）

- 新增 `normalize_host(netloc)`：host 标签级归一化，判据与 path 段同款保守
  风格——**纯 hex ≥10 位**（`_HEX_LABEL_RE`，子域 token 常见 10~12 位，比
  path 段的 ≥16 位放宽，因 DNS 标签本身更短）或 **UUID 形态**的标签归一为
  `<token>`；短标签、含字母表外字符的标签原样保留；
- `normalize_request_key` 的 host 部分改走 `normalize_host`；raw 口径不变，
  token_only_count 自动覆盖 host 维度；
- 不误伤功能子域：`api.`/`static.`/`www.`/`newassets.`/`w.`/
  `collector-pxzc5j78di.` 等均不匹配判据（有测试锁定）。

### 验证

- 单测：新增 10 条（TestNormalizeHost 7 + key 归一化 2 + compare 1），
  加 vm_loop 修复的 8 条（TestRedirectPassthrough），
  **全量 42 条通过，零回归**（原 24 条全保留）：
  `PYTHONPATH=.:integrations/camoufox-reverse-mcp/src:pythonlib
  /usr/local/bin/python3.12 -m pytest scripts/tests`；
- phase13 hcaptcha 已采产物复跑（不重开浏览器，直接对两 run 目录的
  raw/mcp-network 请求记录调 compare）：
  - 修复前：`a18d2c081ece.`/`3887811aa4f2.` 等子域 token 各成独立 key，
    token_only_count=0，虚差计入「真实差异」；
  - 修复后：子域 token 归一为 `<token>.w.hcaptcha.com`，
    **token_only_count=4，虚差消除**；
  - 剩余差异为真实信号（插桩侧 10s 窗口缺 checksiteconfig POST 与
    hsw.js，即缺口 3 的 widget 初始化疑点），裁决仍为 diverged——
    归一化只吞噪音，不吞真实差异 ✅。
  - 复跑报告：`artifacts/analysis/phase13-repro/hcaptcha-divergence-renormalized.json`。

## 产物清单

| 内容 | 路径 |
|---|---|
| 302 复现脚本 | `artifacts/analysis/phase13-repro/repro_302_chain.py` |
| 302 复现报告 ×4 + 修复后验证 ×1 | `artifacts/analysis/phase13-repro/live302-chain-repro*.json` |
| hcaptcha 归一化复跑报告 | `artifacts/analysis/phase13-repro/hcaptcha-divergence-renormalized.json` |
| 改动：divergence 归一化 | `scripts/reverse-browser-instrumentation-divergence.py`（normalize_host） |
| 改动：route 302 直通 | `integrations/camoufox-reverse-mcp/src/camoufox_reverse_mcp/tools/vm_loop.py`（_redirect_passthrough + max_redirects=0） |
| 新增测试 | `scripts/tests/test_reverse_browser_instrumentation_divergence.py`（+10）、`scripts/tests/test_vm_loop_redirect_passthrough.py`（+8） |
