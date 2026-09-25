# 第十三阶段 P2：PerimeterX / Incapsula / F5-Shape / hCaptcha / Arkose 五厂商实测探针

日期：2026-09-26 ｜ 状态：实测完成（五目标被动会话 + 插桩分叉裁决，全数据来自本轮实测）

目的：按能力模型 §6 的 P2 候选池做五家反爬/验证码厂商的被动产物完备性实测，
与第十二阶段 P1（Cloudflare/DataDome/Akamai）合流成形态覆盖矩阵。纪律：全程
被动观察，不提交凭证、不注册、不点风控按钮；评分按
[能力模型 §5 十条清单](vm-reverse-capability-model-2026-09-25.md) 逐条打。

环境：Camoufox `whitenightshadow/152.0.4-beta.30-reverse.9`，headless；被动探针
os_type=windows / locale=en-US（divergence 脚本固有默认 macos，两侧同参可比），
海外流量走 `socks5://127.0.0.1:7890`。探针脚本沿用 P1 同款（签名清单扩展 PX /
reese84 / Shape / hCaptcha / Arkose 关键词）：
`artifacts/analysis/phase13/probe_passive.py`。

## 0. 结论速览

| 目标 | 厂商确认 | 被动结果 | divergence 裁决 |
|---|---|---|---|
| www.zillow.com | PerimeterX/HUMAN（curl 预检 `x-px-blocked: 1`；会话内 `_px3`/`_pxvid`/`pxcts` + px-cloud.net 全链路） | ✅ 首页完整渲染，150 请求，collector POST 捕获 | **aligned**（19 vs 19，多重集与状态分布完全一致） |
| www.imperva.com | Incapsula（`visid_incap_`/`incap_ses_` cookie + `x-iinfo` 头 + reese84 下发） | ✅ 首页完整渲染，182 请求，reese84 cookie 捕获 | **minor**（差 0.6%，3 条真实差异均非风控链路） |
| www.southwest.com | F5/Shape（`/di/swadc/` 传感器 + beacon iframe + sRpK8nqm_sc cookie） | ✅ 首页完整渲染，117 请求，beacon POST body 捕获 | **diverged**（差 21.7%，插桩侧多出 9 条 analytics chunk） |
| accounts.hcaptcha.com/demo | hCaptcha（js.hcaptcha.com/api.js + w.hcaptcha.com widget + checksiteconfig） | ✅ 演示页+widget 渲染，8 请求全 200 | **diverged**（8 vs 5，插桩侧缺 checksiteconfig/hsw.js） |
| signup.live.com | **厂商纠偏：2026 实测为 HUMAN/PX**（hsprotect.net + px-cloud.net），非 Arkose | ✅ 注册页渲染，28 请求，PX `/api/v2/msft` POST 捕获 | **diverged**（差 10.7%，插桩侧未遍历 302 链） |

厂商归属校正（本轮实测）：
- **signup.live.com 注册落地页 2026 年实际是 HUMAN/PerimeterX**：
  `iframe.hsprotect.net`、`client.hsprotect.net/PXzC5j78di/main.min.js`、
  `collector-pxzc5j78di.hsprotect.net/api/v2/msft`、`fst-ec.perimeterx.net`、
  `dvp.px-cloud.net` 全在产物；全产物 grep 无 arkoselabs/funcaptcha。Arkose
  在 live.com 流程更深位置（提交邮箱后），纪律限制本轮不提交表单，**Arkose
  厂商覆盖记 partial**（产物目录名沿用任务分配的 `arkose/`）。
- turbofuture.com（任务建议的 Incapsula 目标）已迁离：301 到
  discover.hubpages.com（Varnish/Fastly）。改用 Imperva 自家站点
  www.imperva.com（curl 预检 visid_incap/x-iinfo 实锤）。
- t-mobile.com 实测为 Akamai（edgesuite 403 + `_abck` + `bm_sz`），非 Imperva。

## 1. 目标一：PerimeterX / HUMAN（www.zillow.com）

curl 预检：HTTP 403 + `x-px-blocked: 1`（CloudFront 边缘 PX 拦截 curl UA）。

### 1.1 被动会话（capture_profile=raw + network_capture capture_body=True，零 hook）

- **首页完整渲染**（`Zillow: Real Estate, Apartments, Mortgages & Home Values`，
  正文真实内容），150 请求：{200: 92, 206: 1, None: 57}，27.2s。
- PX 链路全在产物：`js.px-cloud.net` 主传感器 iframe（`?t=d-jncu6zpow-…`）、
  `ri.px-cloud.net/index.js`、`ift.px-cloud.net/ns`、
  `collector-pxhyx10rg3.px-cloud.net/api/v2/collector` 与 `/b/s` POST 在案。
- PX 凭证三连下发：`_px3`（长签名 cookie）、`_pxvid`、`pxcts` 均在
  document.cookie 可读。
- 111 条 initiator 栈；终态字段落地（terminal_state: answered 92 / failed 58，
  见「正向发现」）。
- profile 自描述：manifest `status=incomplete`、`event_loss=0`；
  raw/capture-status.json 登记 1 条「request has no response or failure event
  at shutdown」——incomplete 有明确归因 ✅。
- 产物：`artifacts/analysis/phase13/perimeterx/`（passive_report.json、
  network_requests.json、project/runs/9831a293…/，87 MB / 2277 文件）。

### 1.2 divergence 裁决：aligned

报告：`artifacts/analysis/phase13/perimeterx/divergence/validation/a7c1e78209334015af56345a094dcdea/instrumentation-divergence.json`。

- 请求数 19 vs 19，差 0.0%；归一化多重集与状态分布完全一致
  （{403:1, 200:17, None:1} 两侧相同）。
- 注意（诚实登记）：divergence 两会话终点均为 `Access to this page has been
  denied`（PX 拦截页），与被动探针的完整渲染不同——两侧**一致地被拦**，
  裁决语义不受影响（比的是两侧差异），疑似 divergence 脚本 os=macos 指纹
  或短窗口触发 PX 拦截，非插桩引入。
- 插桩侧 route_stats：route_hits=18，parse_failures=0，js_rewritten=3，
  loops=103，route_errors=0。**vm_loop 对 PX 主世界脚本正常改写且未被侦测**——
  与 Cloudflare/DataDome 的 diverged 形成鲜明对照。

### 1.3 十条评分

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | js/ri.px-cloud.net script 全文在；eval/iframe 内动态通道被动无产物 |
| 2 循环轨迹 | unknown | 被动不装 vm_loop；divergence=aligned 表明插桩轨迹对本目标可信可得（loops=103 已录） |
| 3 派发裁决 | unknown | 同上，补一轮插桩深采即可升 observed |
| 4 opcode 清单 | unknown | 同上 |
| 5 参数落地+initiator | ✅ | collector `/api/v2/collector`、`/b/s` POST 在案 + 111 条 initiator 栈；`_px3` 签发链可溯 |
| 6 采集点关联 | unknown | 被动无属性事件 |
| 7 redirect 链字节 | ✅ | 无 redirect 链；1 条 shutdown 未完成请求已自描述 |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | unknown | PX 传感器 iframe（js.px-cloud.net）网络层覆盖 ✅，iframe 内执行轨迹待插桩深采；0 条 wasm 请求 |
| 10 时序对齐 | unknown | 被动无 JS 轨迹 |

## 2. 目标二：Incapsula / Imperva reese84（www.imperva.com）

curl 预检：200 + `visid_incap_2439` / `incap_ses_1137_2439` cookie + `x-iinfo` 头。

### 2.1 被动会话

- **首页完整渲染**（`Cyber Security Leader | Imperva, Inc.`），182 请求：
  {200: 134, None: 48}，27.2s。
- Incapsula 链路全在产物：`visid_incap_`/`incap_ses_` cookie 下发；
  **reese84 cookie 已签发**（四段签名长值，document.cookie 可读）；
  混淆传感器脚本 URL 在案（`/strants-not-worstling-We-what-her-Lords-Thunderd`
  ——Incapsula 标志性的随机词拼接传感器路径）。
- 24 条 initiator 栈；0 条 wasm；观测窗内未见 reese84 传感器 POST
  （cookie 已签发但遥测回传未在 18s 窗口触发）。
- profile 自描述：manifest `status=complete`、`event_loss=0`。
- 产物：`artifacts/analysis/phase13/incapsula/`（project/runs/…/，71 MB /
  2426 文件）。

### 2.2 divergence 裁决：minor

报告：`artifacts/analysis/phase13/incapsula/divergence/validation/4fc2ab52ecc945748ac5d7a5c96e9bcb/instrumentation-divergence.json`。

- 请求数 179 vs 180，差 0.6%；两侧终点一致（同一首页标题）。
- 归一化真实差异 3 条：被动侧 1× wp-rocket beacon JS，插桩侧 2×
  admin-ajax.php POST（wp-rocket 预载遥测）——WordPress 插件层噪音，
  均非风控链路。
- 插桩侧 route_stats：route_hits=172，parse_failures=7（全部
  parse_failures_syntax，生产 bundle 语法），js_rewritten=5，loops=23，
  route_errors=0。
- 加密参数签名 0→0（reese84 走 cookie 与 iframe 通道，URL/body hint 不覆盖）。

### 2.3 十条评分

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | 182 请求中 script 全文在（含混淆传感器路径）；eval 通道被动无产物 |
| 2 循环轨迹 | unknown | divergence=minor，插桩轨迹可信可得（loops=23 已录） |
| 3 派发裁决 | unknown | 同上 |
| 4 opcode 清单 | unknown | 同上 |
| 5 参数落地+initiator | unknown | reese84 cookie 值在案，但传感器遥测 POST 未在窗口触发——目标没跑这段代码，非能力缺口 |
| 6 采集点关联 | unknown | 被动无属性事件 |
| 7 redirect 链字节 | ✅ | manifest complete，无缺口待登记 |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | unknown | 1 个 youtube embed iframe（非风控）；0 条 wasm |
| 10 时序对齐 | unknown | 被动无 JS 轨迹 |

## 3. 目标三：F5 / Shape Security（www.southwest.com）

curl 预检：代理下 curl 无响应（curl 特有，浏览器正常）。

### 3.1 被动会话

- **首页完整渲染**（`Southwest Airlines | Airline Tickets and Low Fares`），
  117 请求：{200: 90, 304: 1, None: 26}，22.6s。
- Shape 链路全在产物：`/di/swadc/cdn/cs/pXS4eU1UtrsWT1A6lR5H5EQACPk.js`
  传感器全文、`/di/swadvc/cc.js`、beacon iframe
  `/di/swadc/beacon/bf/bf.html`、**4 条 `POST /di/swadc/beacon/et` 遥测
  （has_engine_request_body=True，请求体在产物）**；Shape 会话 cookie
  `sRpK8nqm_sc` 下发。
- 56 条 initiator 栈；0 条 wasm。
- profile 自描述：manifest `status=complete`、`event_loss=0`。
- 产物：`artifacts/analysis/phase13/f5-shape/`（73 MB / 1664 文件）。

### 3.2 divergence 裁决：diverged

报告：`artifacts/analysis/phase13/f5-shape/divergence/validation/a58fa6383f3846a787f1b26b3d19707d/instrumentation-divergence.json`。

- 请求数 92 vs 112，差 21.7%；两侧终点一致（同一首页标题）；无新增错误状态。
- 差异主体：**插桩侧多出 9 条
  `/swa-resources/scripts/analytics/…/RC*-source.min.js`**——route 层改写导致
  analytics chunk 重取/缓存失效疑点（缺口 2），单次运行未复现确认。
- 首跑 MCP stdio 断管一次（"MCP server closed stdout"），pkill 清理后重跑
  正常——与 P1 环境观察 #10 同类，再发一次（缺口 4）。
- 插桩侧 route_stats：route_hits=86，parse_failures=16（全 syntax），
  js_rewritten=5，loops=36。

### 3.3 十条评分

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | Shape 传感器 js 全文在；eval 通道被动无产物 |
| 2 循环轨迹 | ❌ | 被动无；divergence=diverged 使插桩轨迹对本目标可信度存疑 |
| 3 派发裁决 | ❌ | 同上 |
| 4 opcode 清单 | ❌ | 同上 |
| 5 参数落地+initiator | ✅ | 4 条 beacon `/et` POST 请求体全文 + 56 条 initiator；响应 status 为 None（在途）但终态字段已归因 |
| 6 采集点关联 | unknown | 被动无属性事件 |
| 7 redirect 链字节 | ✅ | manifest complete |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | unknown | beacon iframe 网络层在；iframe 内执行轨迹因 diverged 存疑；0 wasm |
| 10 时序对齐 | unknown | 被动无 JS 轨迹 |

## 4. 目标四：hCaptcha（accounts.hcaptcha.com/demo）

curl 预检：200（Cloudflare 边缘托管，CSP 白名单 *.hcaptcha.com）。

### 4.1 被动会话

- **演示页 + checkbox widget 完整渲染**（`hCAPTCHA Demo`），8 请求全 200，
  21.3s：js.hcaptcha.com/1/api.js、newassets.hcaptcha.com 的 hcaptcha.html
  widget 文档 ×2、每会话独立子域的 w.hcaptcha.com logo、
  **`POST api.hcaptcha.com/checksiteconfig`（widget 配置调用，200）**、
  **`newassets.hcaptcha.com/c/<hash>/hsw.js`（hCaptcha 核心安全 JS，全文在
  产物）**。
- 4 条 initiator 栈；manifest complete、event_loss=0；零 hook 下零错误。
- 产物：`artifacts/analysis/phase13/hcaptcha/`（12.5 MB / 130 文件）。

### 4.2 divergence 裁决：diverged

报告：`artifacts/analysis/phase13/hcaptcha/divergence/validation/2fb36fbb6531405d869d20f83bd539c6/instrumentation-divergence.json`。

- 请求数 8 vs 5，差 37.5%；两侧终点一致（同一演示页标题）；无错误状态。
- 真实信号：**插桩侧 10s 窗口内未发出 checksiteconfig POST、未加载
  hsw.js**——widget iframe 初始化被延迟/阻断疑点（缺口 3）。被动侧这两条
  都在。需第二次插桩运行复现确认后定级。
- 噪音成分：w.hcaptcha.com 每会话独立子域（a18d2c081ece. vs
  3887811aa4f2.）导致 logo.png 虚差——归一化只处理 path token，不处理
  host token（缺口 1）。
- 插桩侧 route_stats：route_hits=5，documents_rewritten=2，parse_failures=0，
  js_rewritten=1，loops=14。

### 4.3 十条评分

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | hsw.js/api.js 全文在；widget iframe 内动态通道被动无产物 |
| 2 循环轨迹 | ❌ | 被动无；divergence=diverged（插桩侧 widget 未初始化） |
| 3 派发裁决 | ❌ | 同上 |
| 4 opcode 清单 | ❌ | 同上 |
| 5 参数落地+initiator | ✅ | checksiteconfig POST 在案 + initiator（被动侧）；hsw.js 全文 |
| 6 采集点关联 | unknown | 被动无属性事件 |
| 7 redirect 链字节 | ✅ | 无 redirect；manifest complete |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | ❌ | widget 是跨域 iframe（newassets.hcaptcha.com）：网络层覆盖 ✅，但插桩侧 iframe 未初始化→iframe 内执行轨迹不可得且影响 widget 行为本身 |
| 10 时序对齐 | unknown | 被动无 JS 轨迹 |

## 5. 目标五：Arkose（signup.live.com）→ 实测纠偏为 HUMAN/PX

curl 预检：302 → login.live.com/login.srf（正常注册流程入口）。

### 5.1 被动会话

- **注册页完整渲染**（`Create your Microsoft account`），28 请求：
  {302: 2, 200: 23, None: 3}，22.1s。
- **厂商纠偏**：落地页风控为 HUMAN/PerimeterX——`iframe.hsprotect.net`
  （app_id=PXzC5j78di）、`client.hsprotect.net/.../main.min.js`、
  `stk.hsprotect.net/ns`、`fst-ec.perimeterx.net`、`dvp.px-cloud.net/v`；
  **3 条 `POST collector-pxzc5j78di.hsprotect.net/api/v2/msft` 全部 200 且
  请求体在产物**（has_engine_request_body=True）。全产物 grep 无
  arkoselabs.net / funcaptcha。
- Arkose 在 live.com 注册流程更深位置（提交邮箱后触发），被动纪律不提交
  表单 → **Arkose 厂商覆盖本轮 partial**。
- 另有 fpt.live.com（微软第一方指纹 iframe）与 df.cfp.microsoft.com 在案。
- profile 自描述：manifest `status=incomplete`、`event_loss=0`；
  raw/capture-status.json 登记 1 条「Response body is unavailable for
  redirect responses」（302 链路，已知模式）——有明确归因 ✅。
- 产物：`artifacts/analysis/phase13/arkose/`（24.5 MB / 417 文件）。

### 5.2 divergence 裁决：diverged

报告：`artifacts/analysis/phase13/arkose/divergence/validation/08c35f4a6a164e54b86a3c38f00ce103/instrumentation-divergence.json`。

- 请求数 28 vs 25，差 10.7%；无新增错误状态。
- 真实信号：被动侧 302 链（login.srf → signup.live.com/?lic=1）在插桩侧
  **未遍历**——插桩侧终点停在 `signup.live.com/`（无 ?lic=1），且缺
  `login.microsoftonline.com/.../risk/initialize` OPTIONS 预检（缺口 5，
  route 层与 302 document 导航交互疑点）。
- 插桩侧 route_stats：route_hits=22，parse_failures=0，js_rewritten=5，
  loops=132。

### 5.3 十条评分（按实测到的 HUMAN/PX 链路打）

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | hsprotect main.min.js 全文在；iframe 内通道被动无产物 |
| 2 循环轨迹 | ❌ | 被动无；divergence=diverged（302 链未遍历） |
| 3 派发裁决 | ❌ | 同上 |
| 4 opcode 清单 | ❌ | 同上 |
| 5 参数落地+initiator | ✅ | 3 条 `/api/v2/msft` collector POST 请求体全文 + 14 条 initiator，响应 200 完整 |
| 6 采集点关联 | unknown | 被动无属性事件 |
| 7 redirect 链字节 | ✅ | 302 链请求/响应头在，redirect body 缺失已自描述 |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | unknown | hsprotect/fpt 双 iframe 网络层在；iframe 内轨迹因 diverged 存疑；0 wasm |
| 10 时序对齐 | unknown | 被动无 JS 轨迹 |

## 6. 新发现缺口清单

### 浏览器能力缺口

1. **（待复现）hCaptcha 插桩侧 widget 初始化延迟/阻断**：10s 窗口内插桩侧
   未发 checksiteconfig POST、未加载 hsw.js，被动侧均在。疑似 route 层改写
   与 widget iframe 引导的交互问题；需第二次插桩运行复现确认后定级。
2. **（待复现）F5/Shape 插桩侧多出 9 条 RC\*-source.min.js analytics chunk
   请求**（被动侧 0 条）。疑似改写导致 chunk 缓存失效重取；单次运行，
   未复现确认。
3. **（待复现）live.com 插桩侧 302 重定向链未遍历**：被动侧
   login.srf→?lic=1 两步 302 + risk/initialize 预检在案，插桩侧全部缺失，
   终点停在无参 URL。route 层与 document 级 302 导航的交互疑点。

### 工具口径缺口（非浏览器）

4. **divergence 归一化不覆盖每会话子域噪音**：hCaptcha 的 w.hcaptcha.com
   每会话独立子域（a18d2c081ece. vs 3887811aa4f2.）产生虚差；归一化只
   处理 path 段 token，不处理 host 段。建议对「单标签 hex ≥10 位的子域
   标签」做同类归一。
5. **MCP stdio 断管再发**：F5/Shape divergence 首跑 "MCP server closed
   stdout"，pkill 清理残留后重跑正常——P1 环境观察 #10 的第二次出现，
   建议升级登记为「可复现倾向」并排查残留进程清理时序。

### 目标侧行为（如实登记，非浏览器缺口）

6. signup.live.com 注册落地页 2026 年为 HUMAN/PerimeterX（hsprotect.net），
   Arkose 在提交邮箱后的更深流程——被动纪律下本轮 Arkose 厂商覆盖 partial。
7. turbofuture.com 已迁离 Incapsula（301→discover.hubpages.com，Varnish）；
   t-mobile.com 实测为 Akamai。P2 候选池的公开情报与 2026 年现状有漂移。
8. zillow 在 divergence 会话（os=macos、短窗口）两侧一致被 PX 拦到
   `Access denied` 页，被动探针（os=windows、18s）完整渲染——PX 对指纹/
   窗口敏感，但两侧一致，裁决语义不受影响。
9. imperva.com 18s 窗口 reese84 cookie 已签发但传感器遥测 POST 未触发——
   需要更长窗口或交互才能触达 reese84 回传链路（纪律限制本轮不交互）。

### 正向发现（P1 缺口修复验证）

10. **P1 缺口 1「在途/中止请求不自描述」已修复**：本轮
    list_network_requests 记录含 `terminal_state`（answered/failed）、
    `failure_reason`、`pending_at_close` 字段；五家会话 None 状态请求均有
    终态归因（如 perimeterx  answered 92 / failed 58），且 manifest
    incomplete 均能在 raw/capture-status.json 找到逐条归因。

## 7. 产物索引

| 目标 | 目录 |
|---|---|
| PerimeterX (zillow) | `artifacts/analysis/phase13/perimeterx/`（passive_report.json / network_requests.json / project/ / divergence/） |
| Incapsula (imperva.com) | `artifacts/analysis/phase13/incapsula/`（同构） |
| F5/Shape (southwest) | `artifacts/analysis/phase13/f5-shape/`（同构） |
| hCaptcha (demo) | `artifacts/analysis/phase13/hcaptcha/`（同构） |
| Arkose→HUMAN (live.com) | `artifacts/analysis/phase13/arkose/`（同构，目录名沿用任务分配） |
| 探针脚本 | `artifacts/analysis/phase13/probe_passive.py` |

## 8. 与 P1 合流的形态矩阵

| 厂商 | 实测目标 | divergence | 插桩可信度 |
|---|---|---|---|
| Cloudflare | turnstile.zeroclover.io | diverged | 挑战遍历未完成 |
| DataDome | leboncoin.fr | diverged | 新增 16×404 疑点 |
| Akamai | zappos.com | minor | 透明 |
| **PerimeterX/HUMAN** | zillow.com | **aligned** | **完全透明** |
| **Incapsula reese84** | imperva.com | **minor** | **基本透明** |
| **F5/Shape** | southwest.com | diverged | analytics chunk 疑点 |
| **hCaptcha** | accounts.hcaptcha.com/demo | diverged | widget 初始化疑点 |
| HUMAN (live.com) | signup.live.com | diverged | 302 链疑点 |
| Arkose | —（未触达） | partial | 需交互流程目标 |
