# 第五阶段计划：多目标 VM 实验矩阵（能力爬坡）

> 前置：能力模型已定义（`docs/vm-reverse-capability-model-2026-09-25.md`），
> 第四阶段产出物完备性已收口。本阶段按能力模型第 5 节的 10 条打分清单，
> 对 P0 目标逐个实测，缺口回流为浏览器能力改动。

**Goal:** 用多个不同 VM 形态的真实站点检验浏览器的产物完备性，
每跑一个目标出一份覆盖评分卡，发现的缺口按优先级修复，
最终让浏览器具备跨形态的 VM 逆向证据采集能力。

**Architecture:** 新增一个**通用目标探针**（`scripts/reverse-browser-vm-target-probe.py`，
目标无关）；目标特定的分析仍只进 `adapters/<target>/`。探针跑完自动生成
`derived/coverage-scorecard.json`（10 条清单逐条 ✅/❌/needs-adapter）。

**纪律：** 被动观察（不提交真实凭证、不触发风控动作）；国内站点直连，
Google 系走 7890 代理；raw 只追加；评分卡如实记 unknown 不猜。

## Task 1: 通用目标探针 + 计划 ✅ 已完成

- Create: `scripts/reverse-browser-vm-target-probe.py`——参数化目标 URL，
  安装 vm_loop_trace（全文档 route、tick_times、max_states=200000），
  被动加载、等待 VM 初始化、drain 全 realm 轨迹、网络全文、原生 trace，
  关闭后自动生成覆盖评分卡
- 评分卡的通用 10 条：动态源码全文率、循环截断、realm 覆盖
  （Worker/iframe 用了的都插桩了吗）、wasm 使用检测（经网络请求
  .wasm 签名）、加密参数候选字段定位（x-bogus/msToken/signature 等
  签名扫描）、redirect 链自描述、原生事件零丢失、时序对齐字段在位

## Task 2: 字节系站点实测（集中式 JSVMP 形态）✅ 已完成

- 目标：抖音 web（匿名加载，被动观察 X-Bogus/msToken 生成链路）
- 产出评分卡；重点看集中式 while+switch 派发形态的 tick 覆盖质量
- 缺口回流：预期触发「外部 script src 插桩」「值级关联」等 P1 缺口

**实测结果（session `douyin-probe-20260925f`，评分 6/10）：** 42 循环 /
335 841 迭代 / 0 截断 / 97 条动态源码全文 / 21k 原生事件 0 丢失。
首测实锤两个缺口并当场回流修复（Task 3）；登记 wasm 盲区
（pylon-wasm/index_v2.wasm）与 data: URL Worker 形态。

## Task 3: 缺口回流（浏览器能力改动）✅ 第一轮已完成

- 按 Task 2 评分卡的 ❌/unknown 项修复，优先级按能力模型第 7 节

**第一轮修复：** ①route 层外部 JS 响应插桩（`rewrite_js_body`，
content-type 与 .js 后缀双通道）；②loop_rewriter 扩展 while(1) 与
for(;;) 形态；③运行时 tick 双签名兼容（语句形态 route 注入）；
④data: URL Worker 解码插桩（抖音嵌套 Worker）；⑤评分卡零长度标记
与 wasm 盲区诚实化。MCP 119、node 全过；复测 worker-gap 归零、
评分 5→6（wasm 诚实扣分）。

## Task 4: 瑞数站点实测（动态再生 + 补环境对抗形态）✅ 已完成（边缘拦截结论）

- 候选：瑞数保护的公开站点（412 签名识别）；每次下发代码不同，
  验证全文捕获与哈希记账；补环境对抗场景看属性事件层覆盖

**实测结果：** 三个公开确认瑞数目标全部在边缘层被拒，JS 零执行——
jzsc.mohurd.gov.cn（TLS 证书错误页）、zxgk.court.gov.cn（403）、
gsxt.gov.cn（403「请求异常」，响应体全文已捕获）。结论：**瑞数形态
的前置门槛是边缘通过性（TLS 指纹/IP 信誉），不是 VM 插桩**——
Botgate 网关在 JS 下发之前就拒绝了我们的浏览器指纹。能力模型登记
为第 0 层前提（边缘通过性），属环境/指纹层工作，与 VM 证据采集正交。
评分卡 4/10 全部为环境项通过、VM 项 unknown（无 JS 可录）。

## Task 5: reCAPTCHA 实测（同厂商形态差异）✅ 已完成

**实测结果（session `recaptcha-probe-20260925e`，评分 6/10）：** 点击
复选框触发后 50 循环 / 86 966 迭代 / 0 截断 / 50 带时序。逼出并修复
三个真实缺口：①importScripts 通道（Worker 入口只是 loader，主力
848KB 代码经 importScripts 落地）；②frame 内 Worker 的 drain 聚合
（注册表在 frame 侧，顶层 workers[] 收不到）；③注入器 for 头分号
解析只看 paren 深度——`for(R=function(){for(;;){}},k=0;;)` 形态
产生非法 JS，导致整个 Worker 载荷语法错误零执行。修复后真实载荷
458 循环注入、语法校验通过。另加评分卡超限源码与 raw/scripts 的
哈希交叉命中。

- reCAPTCHA v3 公开演示页（走代理）；与 BotGuard 对比形态差异

## Task 6: 收尾 ✅ 已完成

- 每目标评分卡汇总 + 缺口修复对照，写
  `docs/reverse8-phase5-multi-target-2026-09-25.md`；计划勾选；git 不主动提交

**结果：** 报告已写；5 项缺口修复（外链 JS 插桩、tick 双签名、
data: Worker、importScripts 通道、frame 内 Worker 聚合 + for 头解析）；
瑞数边缘拦截结论登记为第 0 层前提；MCP 119 / node 71 全绿。

## 执行顺序

Task 1 → 2 → 3（视缺口大小可穿插）→ 4 → 5 → 6。
