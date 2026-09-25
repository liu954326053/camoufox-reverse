# 第十二阶段 P1：Cloudflare / DataDome / Akamai 三厂商实测探针

日期：2026-09-26 ｜ 状态：实测完成（三目标被动会话 + 插桩分叉裁决，全数据来自本轮实测）

目的：按能力模型 §6 的 P1 候选池（Cloudflare challenge、DataDome、Akamai BMP）
做真实反爬目标的被动产物完备性实测，逼出未知缺口。纪律：全程被动观察，不提交
凭证、不注册、不点风控按钮；评分按
[能力模型 §5 十条清单](vm-reverse-capability-model-2026-09-25.md) 逐条打。

环境：Camoufox `whitenightshadow/152.0.4-beta.30-reverse.9`，headless，
os_type=windows / locale=en-US，海外流量走 `socks5://127.0.0.1:7890`
（出口 IP 实测 81.28.13.73，新加坡）。

## 0. 结论速览

| 目标 | 厂商确认 | 被动结果 | divergence 裁决 |
|---|---|---|---|
| turnstile.zeroclover.io | Cloudflare（cf-mitigated: challenge + challenges.cloudflare.com） | ✅ 挑战 ~8s 无交互通过；源站 502（目标侧） | **diverged**（请求数差 50%，插桩侧 15s 未走完挑战） |
| www.leboncoin.fr | DataDome（datadome cookie + captcha-delivery.com + dd.leboncoin.fr） | ✅ 首页完整渲染，203 请求，传感器 payload 捕获 | **diverged**（差 22.7%，插桩侧新增 16×404） |
| www.zappos.com | Akamai 边缘（akacd cookie + bm_sv）；_abck 未在窗口下发 | ✅ 首页完整渲染，204 请求 | **minor**（差 2.4%，图片/预检噪音级） |

厂商归属校正（本轮实测，纠正常识性假设）：
- **walmart.com 首页实际是 PerimeterX/HUMAN**（collector-pxu6b0qd2s.px-cloud.net、
  ift.px-cloud.net），Akamai 只是 CDN 边缘（akavpau_p2 cookie），未见 _abck。
- **nike.com 2026 年实际是 Kasada**（429 document 到 `/fp?x-kpsdk-v=j-1.2.797`），
  AkamaiGHost 仅为边缘。全产物grep 无 _abck。
- 两者会话产物保留在 `artifacts/analysis/phase12/walmart-px/`（walmart）与
  `artifacts/analysis/phase12/akamai/`（nike 被动，目录名沿用任务分配）。

## 1. 目标一：Cloudflare Turnstile / managed challenge

目标：https://turnstile.zeroclover.io/（curl 预检：HTTP 403 +
`cf-mitigated: challenge`，真实挑战页）。

### 1.1 被动会话（capture_profile=raw + network_capture capture_body=True，零 hook）

- 16 请求：{403: 1, 200: 14, 502: 1}；host 分布 turnstile.zeroclover.io×11、
  challenges.cloudflare.com×5。
- 时序：t=5.2s 标题 `Just a moment...` → t=8.3s 标题 `zeroclover.io | 502: Bad
  gateway`。即 **challenge-platform 流程无交互通过**（edge 放行后回源，源站
  502——目标侧源站故障，非拦截）。Turnstile widget 因源站 502 未渲染。
- 关键厂商证据在产物中：`/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1`
  编排页、challenge-platform 的 `fo/` POST 遥测（含请求体）、
  `challenges.cloudflare.com/turnstile/v0/b/.../api.js` 全文（response_body 在
  mcp-network 记录中逐字可读）。
- profile 自描述：manifest `status=complete`、`event_loss=0`；16 条
  request-metadata / 16 条 response-body / 16 条 initiator 栈 / 5 条
  request-body / 3 条 storage-state。
- 产物：`artifacts/analysis/phase12/turnstile/`（passive_report.json、
  network_requests.json、project/runs/7ac4fd08…/）。

### 1.2 divergence 裁决：diverged

报告：`artifacts/analysis/phase12/turnstile/divergence/validation/c85be231ead543e6bc7c31d6607cd716/instrumentation-divergence.json`。

- 请求数 16（被动） vs 8（插桩），差 50.0%。
- 状态分布：被动 {403:1, 200:14, 502:1} vs 插桩 {403:1, 200:7}。
- 页面终点：被动侧到达源站 502 页；**插桩侧 15s 窗口内仍停在
  `Just a moment...`**——挑战遍历未完成。真实信号在此（请求多重集的
  token 化 URL 差异是每会话一次性 ray/hash 的固有噪音）。
- 加密参数签名两侧均 0（challenge 遥测走 POST body 而非 URL 参数，hint 清单
  不覆盖——见缺口 5）。

### 1.3 十条评分（被动会话产物）

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | script 通道全文在（orchestrate/api.js body 逐字可读）；挑战 iframe 内 eval/Function 动态代码通道在被动模式无产物，无法判定 |
| 2 循环轨迹 | ❌ | 被动不装 vm_loop（设计如此）；divergence=diverged 表明插桩轨迹对本目标不可得 |
| 3 派发裁决 | ❌ | 同上 |
| 4 opcode 清单 | ❌ | 同上 |
| 5 参数落地+initiator | ✅ | challenge-platform `fo/` POST 全文 + 16 条 initiator 栈 |
| 6 采集点关联 | unknown | 被动无属性事件；插桩会话对本目标 diverged |
| 7 redirect 链字节 | ✅ | 403→challenge→回源 502 全链字节在，manifest complete、无缺口待登记 |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | ❌ | 跨域 iframe（challenges.cloudflare.com）网络层覆盖 ✅，但 iframe 内执行轨迹不可得（插桩 diverged）；wasm 无请求→目标未在窗口实例化（非 fail） |
| 10 时序对齐 | unknown | 被动无 JS 轨迹可对齐；原生 ms/timestamp 字段在 |

## 2. 目标二：DataDome（www.leboncoin.fr）

curl 预检：HTTP 403 + `x-datadome: protected` + datadome cookie。

### 2.1 被动会话

- **首页完整渲染**（标题 `leboncoin, site de petites annonces gratuites`，正文
  真实内容），203 请求：{200: 188, 204: 9, 302: 1, 403: 1, None: 4}，27.5s。
- DataDome 链路全在产物：`dd.leboncoin.fr/tags.js`（132 516 字节全文）、
  `ct.captcha-delivery.com/c.js`、`geo.captcha-delivery.com/captcha/?initialCid=…`
  （captcha 资源预载但**未弹验证码**），datadome cookie 已下发。
- **核心遥测捕获**：`POST https://dd.leboncoin.fr/js/` 请求体全文在产物
  （`jspl=gmJWStJvUVBriG8k3…`，content-length 5145，form-encoded），initiator
  记录在案；但**响应缺失**（status/ms/size 全 null，见缺口 2）。
- 唯一 403 是 `auth.leboncoin.fr/api/authorizer/v2/authorize`（未登录用户的
  正常 auth 302/403 往返，目标侧行为，非拦截）。
- profile 自描述：manifest `status=incomplete`、`event_loss=0`；
  capture-status.json 登记 1 条错误「Response body is unavailable for redirect
  responses」（request_id 05628946…）——incomplete 有明确归因 ✅。
  133 个 script 全文、198 条 response-body、203 条 initiator。
- 产物：`artifacts/analysis/phase12/datadome/`（project/runs/9ed8628d…/）。

### 2.2 divergence 裁决：diverged

报告：`artifacts/analysis/phase12/datadome/divergence/validation/540e3b0c25c24f3cbb46433e1882b0e6/instrumentation-divergence.json`。

- 请求数 203 vs 157，差 22.7%；两侧终点一致（同一首页标题）。
- only_passive 差异多为 img.leboncoin.fr 图片变体（懒加载深度波动，目标侧噪音）。
- **插桩侧新增 16×404**，集中在 `auth.leboncoin.fr/_next/static/chunks/*.js`
  （被动侧 0 条 404）——route 层改写与 Next.js chunk 加载的交互疑点，单次
  运行，未复现确认（缺口 1）。
- 插桩侧 route_stats：route_hits=153，**parse_failures=103**，js_rewritten=1，
  loops=10，route_errors=0。生产 bundle 的改写解析覆盖率语义待核实（缺口 3）。
- 加密参数签名 0→0（同 Turnstile，hint 清单口径问题）。

### 2.3 十条评分

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | tags.js/c.js 等 133 个 script 全文在；eval 通道被动无产物 |
| 2 循环轨迹 | ❌ | 被动无；divergence=diverged 使插桩轨迹可信度存疑 |
| 3 派发裁决 | ❌ | 同上 |
| 4 opcode 清单 | ❌ | 同上 |
| 5 参数落地+initiator | ✅ | dd `/js/` POST payload（5145B）全文 + initiator；但响应缺失且未自描述（扣分记在 #7） |
| 6 采集点关联 | unknown | 同目标一 |
| 7 redirect 链字节 | ❌ | redirect body 缺失已自描述 ✅，但传感器 POST 响应缺失**未**自描述（缺口 2）——自描述不完整 |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | unknown | 观测窗 0 条 .wasm 请求（与「DataDome VM+wasm 三层」预期不符：本轮挑战未升级到 wasm 层，活性自检无从触发）；1 个空 src iframe；captcha-delivery 网络层在 |
| 10 时序对齐 | unknown | 同目标一 |

## 3. 目标三：Akamai（www.zappos.com）

选型经过：walmart.com（任务首选）实测为 PerimeterX；换 nike.com 实测为
Kasada；再换 zappos.com（Akamai 边缘确认：akacd_zappos_prod cookie）。
walmart 不属于「blocked」三类换目标准则（它正常加载），此处为厂商归属纠偏，
如实登记：共跑了 4 个被动会话，walmart/nike 数据保留备查。

### 3.1 被动会话

- **首页完整渲染**（`Shoes, Sneakers, Boots, & Clothing + FREE SHIPPING |
  Zappos.com`），204 请求：{200: 196, 206: 2, None: 6}，30.7s。
- Akamai BMP 证据：`bm_sv` cookie 在 document.cookie（BMP 会话校验值）；
  akacd_zappos_prod 边缘 cookie。
- **_abck 未下发**：document.cookie 无 _abck，全产物 grep 无 _abck；观测窗内
  未见传感器 POST。18s 被动首页窗口未触发 _abck 签发——目标侧行为（BMP 传感器
  惰性/首页不采），非拦截。
- profile 自描述：manifest `status=incomplete`、`event_loss=0`；
  capture-status.json 登记 2 条「Response body … was evicted!」（两条首页 mp4
  视频体被驱逐）——incomplete 有明确归因 ✅。62 个 script 全文、196 条
  response-body。
- 产物：`artifacts/analysis/phase12/zappos/`（project/runs/f9f67359…/）。

### 3.2 divergence 裁决：minor

报告：`artifacts/analysis/phase12/zappos/divergence/validation/9dc9366b9d0f42f8897ae27943204dc8/instrumentation-divergence.json`。

- 请求数 205 vs 210，差 2.4%；无新增错误状态；两侧终点一致。
- 差异项全部为 m.media-amazon.com 图片与 amazon.zappos.com CORS 预检——
  加载顺序噪音。**插桩对本目标透明**（与 Kasada/加速乐类形成对照）。
- 加密参数签名 0→0（_abck 未触发，无参数可比）。

### 3.3 十条评分

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序全文 | unknown | 62 个 script 全文在；eval 通道被动无产物（divergence=minor，可放心补采插桩会话） |
| 2 循环轨迹 | unknown | 本轮未跑插桩深采；传感器是否 VM 形态未定（bmp 为混淆 JS + 行为遥测） |
| 3 派发裁决 | unknown | 同上 |
| 4 opcode 清单 | unknown | 同上 |
| 5 参数落地+initiator | unknown | _abck/sensor POST 未在窗口触发——目标没跑这段代码，非能力缺口 |
| 6 采集点关联 | unknown | 同上 |
| 7 redirect 链字节 | ✅ | 无 redirect 链；2 条 mp4 body 驱逐已自描述 |
| 8 事件丢失 | ✅ | event_loss=0 |
| 9 realm 覆盖 | unknown | 无 wasm/Worker 证据；iframe 未见 |
| 10 时序对齐 | unknown | 被动无 JS 轨迹 |

## 4. 新发现缺口清单

### 浏览器能力缺口

1. **在途/中止请求不自描述（实锤）**。list_network_requests 与 mcp-network
   记录中，未拿到响应的请求 status/ms/size 全 null、无原因字段，且
   capture-status.json 的 errors 不登记（datadome 传感器 POST `/js/` 即此类；
   同类 None 状态：datadome 4 条、nike 27 条、zappos 6 条）。分析者无法区分
   「目标中止/挂起」与「捕获丢失」——违反自描述纪律。建议：会话关闭时对
   pending 请求统一登记 `pending-at-close` gap 记录。
2. **插桩侧 auth.leboncoin.fr `_next/static/chunks/*.js` 16×404（疑似，单次
   运行）**。被动侧 0 条 404。route 层改写与 Next.js 子域 chunk 加载的交互
   疑点；需第二次插桩运行复现确认后才能定级。
3. **route 层 parse_failures=103/153 hits（语义待核实）**。DataDome 插桩侧
   106 个 application/javascript 响应中 103 个 parse_failure、仅 1 个改写
   成功。若 parse_failure 语义是「无可注入循环形态」则属正常；若是解析器
   不兼容生产 bundle 语法则是覆盖缺口。需核对 route 改写器实现后定性。

### 工具口径缺口（非浏览器）

4. **divergence 脚本对一次性 token URL 敏感**：Cloudflare 挑战 URL 含每会话
   一次性 ray/hash，请求多重集比较把同一流程的两个会话判成大量 key 差异
   （Turnstile 50% 差中大部分为此类噪音；真实信号是「插桩侧未走完挑战」）。
   建议对 challenge-platform 类 URL 做归一化（去 ray/hash 段）后再比多重集。
5. **加密参数 hint 清单不覆盖 POST body 渠道**：Cloudflare/DataDome 遥测均走
   POST form body（jspl=、challenge payload），hint 只扫 URL/关键字，两侧
   均报 0→0，失去在位率对比意义。建议扫描 request body 前 N 字节。

### 目标侧行为（如实登记，非浏览器缺口）

6. turnstile.zeroclover.io 源站 502：挑战通过后回源失败，Turnstile widget
   本体未渲染——本轮只实测到 managed challenge 层。
7. walmart.com 首页 = PerimeterX/HUMAN（px-cloud.net），nike.com = Kasada
   （x-kpsdk-v=j-1.2.797，429 /fp document）——2026 年厂商归属与公开常识
   不符，能力模型 §6 的 Akamai 候选需要换池。
8. zappos 首页 18s 被动窗口只下发 bm_sv、未签发 _abck、未见传感器 POST——
   BMP 传感器在本窗口惰性。需要带交互/更长窗口的会话才能触达 _abck 链路
   （纪律限制本轮不交互）。
9. DataDome 本轮未升级到 wasm 层（0 条 .wasm 请求）——挑战强度不足以触发
   「VM+wasm 三层」的 wasm 部分，§6 表中「逼出 wasm 能力」的实验价值本轮
   未兑现。

### 环境观察（一次性，未复现）

10. Turnstile 首次探针运行在 navigate 后 MCP stdio 断管（"MCP request could
    not be written"，run b514fbef 残留 partial profile）；同参数重跑完全正常。
    疑似首次运行被杀进程残留干扰，未复现、未定位，如实登记。

## 5. 产物索引

| 目标 | 目录 |
|---|---|
| Cloudflare | `artifacts/analysis/phase12/turnstile/`（passive_report.json / network_requests.json / project/ / divergence/） |
| DataDome | `artifacts/analysis/phase12/datadome/`（同构） |
| Akamai(zappos) | `artifacts/analysis/phase12/zappos/`（同构） |
| walmart(PX 备查) | `artifacts/analysis/phase12/walmart-px/` |
| nike(Kasada 备查) | `artifacts/analysis/phase12/akamai/` |
| 探针脚本 | `artifacts/analysis/phase12/probe_passive.py` |
