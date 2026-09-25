# 第十四阶段：瑞数 P0 全链路实测（sso.cnipa.gov.cn）

日期：2026-09-26 ｜ 状态：实测完成（被动全链路 + divergence 裁决 + 十条评分，全数据来自本轮实测）

目的：补能力模型 §6 目标矩阵中唯一未实测的形态——瑞数信息（RiverSecurity）
保护的政务/金融站点（动态再生 JS + VMP + 412 签名）。纪律：全程被动观察，
不登录、不提交表单、不触发风控动作；国内站点直连（无代理）。
环境：Camoufox `152.0.4-beta.30-reverse.9`，headless，被动探针
os_type=windows / locale=en-US；divergence 脚本固有 macos（两侧同参可比）。
探针脚本：`artifacts/analysis/phase14/probe_passive.py`（phase13 同款，
签名清单扩瑞数关键词）。

## 0. 结论速览

| 项 | 结果 |
|---|---|
| 确认目标 | **sso.cnipa.gov.cn**（国家知识产权局统一身份认证，政务）——瑞数 v5/v6 实锤 |
| 形态指纹 | 412 挑战页含 `$_ts.nsd=107399` / `$_ts.cd="<加密 blob>"` / 动态 meta id / `r='m'` 标记 / `_$kk()` 收尾；动态 JS 路径 `/ghzsswfTX3Of/<随机>.<hash>.js`；签名 cookie `BOJRPc7LzNOPP`（三段点分 base62）；XHR 签名参数 `qco9Ha2n`（值同形态三段点分） |
| 动态再生实锤 | 同目录两份动态 JS：`T5YBOFlgC9X8.f9a8d4a.js`（237,412 B）与 `JglFt3wRizZz.f9a8d4a.js`（178,671 B），每页换名换内容，尾部为控制流置换表（`…],[3,0,2,1,2,],]);}`），全文在案 |
| divergence 裁决 | **diverged**（请求数差 94.3%，插桩侧 412 收到、动态 JS 改写后挑战未完成；route_stats：js_rewritten=1、loops=5、parse_failures=0、route_errors=0） |
| 签名链路闭环 | **网络层闭环**：挑战页全文 → 动态 JS 全文 → 302 放行 → 签名 XHR（initiator 栈直指动态 JS 内 `_$gP … > eval:2`）；采集点→变换的值级闭环需插桩侧，本轮 diverged 不可得 |
| 十条评分 | ✅×4 / unknown×3 / gap×3（详见 §4） |

### 厂商归属校正与目标池迁移（本轮实测）

- **wcjs.sbj.cnipa.gov.cn 主站 2026 实测不是瑞数**：挑战为 406 +
  `/_fec_sbu/fec_wrapper.js` + `hxk_fec_16b50213.js`（254 KB 动态 JS）+
  `FECU=` XHR 参数，与 2026 年国航 FECU 逆向公开实录同源（网宿系动态防护，
  响应经 `x-via: PS-000-…Cdn Cache Server` 网宿 CDN），**登记为网宿系形态**
  （hxk_fec/FECU 家族）。但同会话跳转链上的 **sso.cnipa.gov.cn/login 是瑞数**
  ——412 页 `$_ts` 签名是瑞数 v5/v6 的判据级特征。
  [CSDN 国航 FECU 逆向](https://gitcode.csdn.net/69e88e6854b52172bc6b833b.html "citation")
- 公开情报中的经典瑞数目标 2026 实测首页均无挑战（curl + 浏览器双侧）：
  wenshu.court.gov.cn（48 请求全 200/302，无 cookie 签名）、www.sse.com.cn
  （140 请求全 200，仅 GrowingIO 统计）、www.csrc.gov.cn（阿里 acw_tc）。
  瑞数目标池已迁移，**sso.cnipa.gov.cn 是本轮唯一实测在位的瑞数政务目标**。

## 1. 产物清单

| 产物 | 路径 |
|---|---|
| 被动探针（扩瑞数签名） | `artifacts/analysis/phase14/probe_passive.py` |
| 主会话（wcjs→sso 跳转链，含瑞数全链） | `artifacts/analysis/phase14/sbj/`（passive_report.json、network_requests.json、project/runs/5bf53212…/） |
| 裁判文书网对照（无挑战） | `artifacts/analysis/phase14/wenshu/` |
| 上交所对照（无挑战） | `artifacts/analysis/phase14/sse/` |
| divergence 裁决 | `artifacts/analysis/phase14/sso-ruishu/divergence/validation/7358ba9e…/instrumentation-divergence.json`（schema 2） |

关键原始证据（sbj/project/runs/5bf53212…/raw/network/）：

- `8c71af2b942542ab91f26116b67f8c12/response.body`——wcjs 406 挑战页全文
  （616 B：`#ENCODED#` comUrl div + `/_fec_sbu/` 双 script + 行内 10 段 token）。
- `e3f755ba37194d979339547ab097bd89/response.body`——**sso 412 瑞数挑战页
  全文（2,978 B）**：`<meta id="7w0Srde72Yr6" content="745s_…" r='m'>` +
  `$_ts.nsd=107399;$_ts.cd="<1.5 KB 加密 blob>"` + 动态 script
  `/ghzsswfTX3Of/T5YBOFlgC9X8.f9a8d4a.js` + 尾部 `_$kk();`。
- `967c5263b29d4c6e8ffba051b367cde9/response.body`——瑞数动态 JS 全文
  （237,412 B，引擎层捕获，尾部控制流置换表完整）。
- `6176739210234360a2ea0842514e4070/response.body`——第二份再生 JS 全文
  （178,671 B，/am/ 页加载，同目录换名换内容）。

## 2. 瑞数挑战链时序（被动会话实测）

```
GET /login                     → 412（挑战页：$_ts + meta + 动态 script src）
GET /ghzsswfTX3Of/T5YBO….js    → 200（动态 JS 全文 237 KB）
GET /login                     → 302（挑战通过，签名 cookie BOJRPc7LzNOPP 落地）
GET /am/                       → 200（真实 SPA；加载第二份再生 JS JglFt3….js 179 KB）
XHR GET /isLogin?qco9Ha2n=<三段点分签名> ×2 → 200
```

- 签名 cookie（document.cookie 可读）：`BOJRPc7LzNOPP=0dYYH1P1….caeeIV88….Fp3zBrWl…`
  （动态名 + 三段点分 base62 值，瑞数 v6 形态）；同页另有 `SF_cookie_43`、
  `deviceId` 等。
- XHR 签名参数 `qco9Ha2n`：参数名动态，值同三段点分形态，两条请求值不同
  （每请求重签名）。
- **落地→变换回溯**：两条 /isLogin XHR 的引擎 initiator 栈为
  `_$gP @ https://sso.cnipa.gov.cn/ghzsswfTX3Of/JglFt3wRizZz.f9a8d4a.js line 5 > eval:2`
  ——签名 XHR 由瑞数动态 JS 内部（eval 通道）发起，网络层链路证据闭环。
- 挑战页/动态 JS 均为导航与 parser 触发，无 initiator 栈（符合预期）。

## 3. divergence 裁决：diverged

报告：`…/sso-ruishu/divergence/validation/7358ba9ed6284a8ca51f6d0fc0927b98/instrumentation-divergence.json`。

| 侧 | 请求数 | 状态分布 | 终点 |
|---|---|---|---|
| 被动 | 35 | {412:1, 200:30, 302:1, 301:1, None:1, 206:1} | /am/#/login（挑战通过，SPA 完整渲染） |
| 插桩 | 2 | {412:1, 200:1} | /login（挑战页，title 空，停滞） |

- 归一化后 33 条真实差异（仅被动侧走完后半程），token 虚差 0 条——
  子域/path token 归一化在本形态无虚差可吸收（差异不是 token 轮替，是挑战没走完）。
- 插桩侧 route_stats：route_hits=2、js_rewritten=1、loops=5、parse_failures=0、
  route_errors=0、worker_urls=[]。**vm_loop 对瑞数动态 JS 改写成功且录到 5 个
  循环，之后目标静默终止挑战流程**（无第二次 /login、无 cookie 落地）。
- 判读（分级）：observed=改写注入本身未报错（parse_failures=0）；inferred=
  瑞数 v5/v6 动态 JS 含自校验/反插桩（公开情报：函数 toString 完整性、时序
  校验、无限 debugger），route 层文本改写改变脚本字节即被检出并静默中止——
  与 Kasada/gsxt 同类「改写敏感」行为。**处置建议沿用能力模型：该形态走被动
  profile**（与 Cloudflare managed challenge、DataDome 同档）。

## 4. 十条评分（能力模型 §5）

| # | 评 | 证据 |
|---|---|---|
| 1 VM 程序本体全文 | ✅（被动） | 412 挑战页 2,978 B 全文 + 两份动态再生 JS 全文（237 KB / 179 KB，引擎层 body，尾部完整）在案；动态脚本经 eval 执行的内层代码被动无产物（设计边界） |
| 2 循环轨迹 | gap | 插桩侧仅 route_stats 汇总（loops=5），tick 级 trace 未持久化（G4）；挑战 diverged 后无更多循环 |
| 3 派发形态裁决 | unknown | 动态 JS 尾部为控制流置换表（`…],[3,0,2,1,2,],]);}`）， inferred 为平坦化+集中派发（与公开瑞数 v5 情报一致），但无 tick 级 observed 证据 |
| 4 opcode 清单 | gap | 同上，插桩侧挑战未完成 |
| 5 加密参数落地 + initiator 栈 | ✅ observed | `qco9Ha2n` 签名参数落在 /isLogin XHR URL；initiator 栈直指动态 JS `_$gP … > eval:2`；cookie `BOJRPc7LzNOPP` 落地在案 |
| 6 值↔采集点关联 | unknown | 被动会话不装属性事件/taint（设计如此）；插桩侧 diverged 不可得——非 fail |
| 7 redirect 链字节完整 | ✅ | 301→412→200(JS)→302→200(/am/) 全环节在案；412/动态 JS/落地页 body 完整；302 无 body 属正常 |
| 8 原生事件噪音/丢失 | ✅ | 被动会话 errors=0；无 dropped 登记 |
| 9 Worker/iframe/wasm | ✅ | 均无（route_stats worker_urls=[]；页面无 iframe；无 wasm 请求）——无需覆盖项 |
| 10 时序对齐 | ✅ | 网络事件 ms 字段与页面轮询时间线同一 wall-clock 坐标系 |

## 5. 缺口登记

| 编号 | 级别 | 缺口 | 处置 |
|---|---|---|---|
| G1 | observed | **瑞数 v5/v6 对 route 层文本改写敏感**：插桩侧 412 收到、动态 JS 改写后挑战静默终止（diverged 94.3%）。引擎层/高成本 | 登记。能力模型 §3 动态再生行更新为「被动 ✅ / 插桩 diverged（改写敏感，同 Kasada/gsxt 档）」；该形态建议走被动 profile |
| G2 | observed → ✅已修 | divergence 脚本加密参数 hints 不覆盖瑞数动态参数名（`qco9Ha2n` 等每轮换名，url_hits 0→0 失真） | **已修（工具层）**：`_DOTTED_SIG_RE` 三段点分签名值形态兜底（URL+body），+3 测试，`pytest scripts/tests/test_reverse_browser_instrumentation_divergence.py` 37 PASS。已知误中面：JWT 同形态，启发式可接受 |
| G3 | observed | divergence 会话 tick 级 vm_loop trace 未持久化，只有 route_stats 汇总（loops=5 但无 tick 明细可复核） | 登记（工具层，低成本：divergence 脚本 stop 后 dump trace）。本轮未修 |
| G4 | observed | 瑞数目标池 2026 迁移：wenshu/sse/csrc 首页无挑战；wcjs.sbj.cnipa.gov.cn 主站已换网宿系（hxk_fec/FECU） | 登记。能力模型 §6 P0 行目标更新为 sso.cnipa.gov.cn；网宿系（hxk_fec/FECU，406 + `_fec_sbu`）作为新形态候选入池 |
| G5 | observed（小） | `list_network_requests` 的 size 字段封顶 200,000（摘要上限），hxk_fec 254 KB 全文实际完整落盘 raw/response.body——size 字段易误读为截断 | 登记（文档级，非数据缺口） |

## 6. 与既有形态的对比定位

瑞数（sso.cnipa.gov.cn 实测）落入能力模型 §3「动态再生」行的预期风险位：
源码全文+哈希记账天然适配（被动 ✅，两份再生 JS 全文在案），但插桩补丁
锚点/文本改写被目标检出（diverged）。与 Kasada（Worker 盲区收口后通过）
不同，瑞数的检出发生在主世界动态 JS 自校验层，route 改写通道本身即攻击面
——后续若要做插桩深采，方向是引擎层 tick 注入（不经文本改写）而非补丁
参数化，属引擎层高成本项，仅登记。
