# 浏览器 VM 逆向支持能力模型

日期：2026-09-26 ｜ 状态：第十四阶段同步（经 Google BotGuard 四阶段 +
抖音/reCAPTCHA 多目标实测 + wasm/taint-lite/序列设施三轮能力爬坡 +
Kasada/加速乐插桩隐蔽性边界实测 + P1/P2 八厂商实测 + 瑞数 P0 政务站点
全链路实测后抽象——目标矩阵形态全覆盖）

## 0. 一句话定义

**浏览器是 VM 逆向的证据采集基础设施，不是逆向工具本身。**

能力边界：对任意带 VM 保护的目标，浏览器产出**完整、自描述、离线可用**
的记录产物；逆向分析由分析者（人或 AI）在产物上完成。衡量能力的唯一
标准是——**分析者只看产物，不重开浏览器，能把目标的加密逻辑分析到
可复现的程度**。产物里没有的，就是能力缺口；产物里有但没说清可信度的，
也是缺口（自描述纪律）。

两条核心能力线：
1. **VM 逆向支持**：解释器结构、opcode、handler 语义的证据采集；
2. **运行链路监控**：加密参数（token/signature/ sensor data）从环境采集
   到请求落地的全链路记录。

## 1. 从逆向者工作流反推能力需求

逆向者拿到产物后的标准动作链，每一步列出所需证据：

| 步骤 | 逆向者要回答的问题 | 所需产物 |
|---|---|---|
| ① 定位 VM | 哪段代码是 VM？在哪个 realm（主页面/iframe/Worker）？何时执行？ | 动态源码全文（含 eval/Function/TT/script 元素/Worker 各通道）、原生 script enter/exit 事件、realm 清单 |
| ② 还原结构 | 派发形态？handler 表在哪？opcode 集多大？ | 循环清单与角色分类、状态序列、源码全文、派发裁决（集中式/表间接/FSM） |
| ③ 钉语义 | 每个 handler/opcode 干什么？ | 执行序列、参数/返回值、与原生属性事件的因果关联、handler 注册/派发频次 |
| ④ 追数据流 | 加密参数从哪来？采集了什么？经过哪些变换？落到哪个请求字段？ | 指纹采集点记录（属性级事件）、网络请求/响应全文、initiator 调用栈、cookie/存储事件、值级流向关联 |
| ⑤ 验证理解 | 我理解的逻辑能复现吗？ | 完整请求链重放所需的全部字节（含 redirect 链）、时序坐标系（tick_times ↔ 原生 w 字段） |

## 2. 能力域 × 现状矩阵（十阶段实战实测）

| 能力域 | 产物 | 现状 | 实测依据 |
|---|---|---|---|
| 动态代码捕获 | eval/Function/TrustedTypes/script 元素/setTimeout 字符串/Worker 全通道，≤256KB 全文 | ✅ | 第三阶段 25 条源码全文；第四阶段全文策略 |
| Worker realm | Blob/URL/嵌套 Worker 插桩 + drain 消息桥 | ✅ | 第四阶段 e2e：嵌套 Worker 状态聚合 |
| iframe realm | 同源 iframe 运行时复制 + 聚合 drain | ✅ | BotGuard bscframe 实战 |
| 循环级轨迹 | tick 计数 + 状态快照 + 全量落盘 + coverage_pct 自描述 | ✅ | 148 595/148 595 全量，0 截断 |
| 循环角色分类 | string-decoder/bit-reader/cipher/table-permutation 等六类 | ✅（BotGuard 适配器） | 第三阶段 |
| 函数级追踪 | 代码补丁通道：注册/派发计数 + `__mcp_vm_rec` 有序序列与参数证据，跨 realm 合并；`__mcp_vm_rec_ret` 一行锚定返回值（异常原样抛出并记 threw） | ✅ | 第四阶段 handler-counts；第九阶段序列设施；第十二阶段返回值锚定 |
| 原生属性事件 | DOM/BOM 属性读写（o/p/v）+ 时序 | ✅ | PropertyTracer，8226 事件 0 丢失 |
| 网络证据 | 请求/响应全文、initiator 栈、redirect 链自描述；在途/中止请求终态自描述（terminal_state/failure_reason/pending_at_close）；**引擎层 initiator 栈**（http-on-opening-request 同步抓 Components.stack，DevTools 同款机制，零页面世界污染——零容忍目标唯一通道）；**引擎体捕获透出**（juggler stock 已具备 readRequestPostData/ResponseStorage，第十三阶段补 MCP 透出层：engine_request_body/engine_response_body base64 无损 + size/truncated 自描述，二进制请求体不再被 utf-8 解码静默丢弃） | ✅ | 四阶段；第十二阶段：终态自描述 + 引擎栈落地；第十三阶段：引擎体捕获收口（XHR 二进制 `\x00\x01\xff\xfeAB` 无损实测，160 集成测试全绿） |
| 时序关联 | tick_times ↔ 原生 w 字段同一 wall-clock 坐标系 | ✅ | 第三阶段 |
| 噪音过滤 | chrome/resource:// 过滤，derived 视图 | ✅ | 第四阶段（78% 噪音实测） |
| 缺口自描述 | worker-gap / redirect / coverage_pct / level=gap | ✅ | 第四阶段 |
| **wasm** | 五入口插桩：compile/instantiate/compileStreaming/instantiateStreaming + `new WebAssembly.Instance()` 构造器（字节回找去重 bytes_ref、冻结 exports 遮蔽计数）；字节哈希+≤768KB base64、imports/exports 调用计数、跨 realm 聚合 | ✅ | 第六阶段；第十阶段 C1；第十二阶段 Instance 构造器收口（来路不明 module 如实记 bytes_unavailable，不伪造） |
| **opcode 执行序列** | `__mcp_vm_rec` 序列缓冲（5000/realm）+ 参数证据，适配器锚点即得 | ✅ | 第九阶段靶场：8 条序列与程序逐字保序 |
| **参数/返回值** | 参数随序列记录（哈希+预览）；返回值经 `__mcp_vm_rec_ret` 通用设施一行锚定 | ✅ | 第九阶段；第十二阶段 rec_ret（含异常路径记录） |
| **外部 script src 插桩** | route 层改写 JS 响应（while(1)/for(;;) 形态覆盖）；loopback 经 urllib 绕行重取后走同一管线 | ✅ | 第五阶段抖音实测；第十阶段 B：loopback 盲区修复（route_fetch_fallback 可观测），本地靶场内联改写全通 |
| **跨域 iframe / ServiceWorker** | 跨域 iframe 经 postMessage drain 桥聚合（e.source 校验防伪造）；ServiceWorker 引擎不可达，register 如实记 serviceworker-gap | ✅（iframe）/ 🟡（SW 边界登记） | 第十阶段 D：跨域靶场 frame[0]/top 轨迹完整回传 |
| **值级数据流关联** | taint-lite：9 个编码/加密 API 值事件 + fromCharCode 逐字符合并装配链（64 字符粒度）+ charCodeAt/at/codePointAt 逐字符读取合并事件（同串同向连续读取合并、64 码元 flush）+ 快照/片段三级匹配，离线关联器出 `{参数,API/loop,realm,ts}` 表 | ✅ | 第七/八阶段靶场端到端命中；第十阶段 C2：抖音实测 339 次单字符调用合并为 6 条可读装配事件；第十二阶段：读取侧中间态收口；边界：多链交错截断合并粒度、并行装配链不可拆 |
| **补丁锚点鲁棒性** | structural 结构签名锚点：等长归一化 + `_+` 模式 + `/d` 捕获组 span 恢复真名，函数/变量改名免疫（属性改名仍失配，如实限制） | ✅ | 第十阶段 A：改名靶场（Du→Xq）锚点命中、序列完整还原 |
| **插桩分叉检测** | 被动/插桩双会话对比（请求多重集/状态分布/加密参数在位率），aligned/minor/diverged/gap 裁决；schema 2：path token 段归一化 + query 键名多重集 + 加密参数扫 POST body，raw/normalized 双口径对照 | ✅ | 第十阶段 E2；第十一阶段 gsxt diverged；第十二阶段：zappos 复验 minor（插桩透明）、leboncoin 维持 diverged（84 条真实差异，目标侧陈旧预渲染连锁 404） |
| **流式落盘** | payload >32MB 时 loops[].states 拆旁车 NDJSON，主 JSON 留指针；divergence.py 同步兼容 | ✅ | 第十阶段 E1 |
| **toString 伪装守护** | WeakMap 登记包装函数→原生源码串，守护版 FP.toString 命中即返回原生串；name/length/prototype 值对齐、引擎真实自描述串（Firefox 多行格式）、原位描述符替换（不加实例自有属性）、去 AsyncFunction 化 | ✅（对检查包装函数的目标）/ ⛔（对连 FPT 本身都验的目标） | 第十一阶段：405 死循环类侦测可解；gsxt 加速乐纯 FPT 替换即被语法级侦测（构造性/in 操作符），JS 层不可隐形，Proxy 方案楔死 SpiderMonkey |
| **Worker realm（补强）** | route 层 Worker 脚本插桩 + 前置运行时（绕过 CSP connect-src 对页面内 XHR 取源码的拦截）；直通 Worker 入注册表，gap 不静默 | ✅ | 第十一阶段：canadagoose（Kasada）实测通过 |

## 3. VM 形态谱系 × 插桩覆盖矩阵

不同厂商的 VM 架构差异决定插桩策略。谱系（按实测与公开情报）：

| 形态 | 代表 | 我们的覆盖 |
|---|---|---|
| 集中式 while+switch 派发 | 经典 JSVMP（字节 X-Bogus/msToken）、多数混淆器 | ✅ tick 注入原生支持（设计初衷形态） |
| 表间接派发（无集中循环） | Google BotGuard（本 build） | ✅ 经循环角色 + 补丁通道组合裁决 |
| FSM（for(init;;)/while(true) 判别变量） | BotGuard 解码器 | ✅ fsmVarAfter |
| 动态再生（每次下发代码不同） | 瑞数 4/5/6、Kasada 轮换 | ✅ 源码全文 + 哈希记账天然适配；补丁锚点需参数化；sso.cnipa.gov.cn 实测：两份再生 JS（237KB/179KB）全文在案 |
| VM + wasm 混合 | DataDome 2026 三层（VM 混淆 + 动态再生 + wasm）、抖音 pylon-wasm | ✅ wasm 五入口证据（第十二阶段 Instance 构造器收口）；exports 计数不受引擎冻结限制（遮蔽方案） |
| 强反调试/反插桩 | Kasada（canadagoose 实测） | ✅ Worker 盲区收口后插桩链路通过（第十一阶段） |
| 页面世界零容忍（连 FPT 本身都验） | 加速乐（gsxt.gov.cn 实测） | ⛔ JS 层插桩不可隐形（语法级侦测）；✅ 被动捕获 profile 完整产出 + 分叉检测裁决 diverged（第十一阶段定案）；第十二阶段引擎层 initiator 栈为零容忍目标补上落地调用点证据 |
| 补环境对抗（无 VM 壳但重度环境探测） | 瑞数 Cookie2 生成 | ✅ 属性事件层覆盖采集点 |
| 挑战页（managed challenge） | Cloudflare Turnstile（zeroclover 演示站实测） | ✅ 被动 8s 无交互通过、挑战链路字节完整；⚠️ 插桩侧 15s 未走完挑战（diverged），建议该形态走被动 profile |
| 传感器 POST + 动态再生 | DataDome（leboncoin.fr 实测） | ✅ 被动全量渲染 + 5145B 加密传感器 payload 全文捕获；⚠️ 插桩触发上游陈旧预渲染连锁 404（route 重发缺 Priority 头，平台层限制登记），diverged |
| 传感器数据 + 行为遥测 | Akamai BMP（zappos.com 实测） | ✅ 插桩透明（divergence=minor，2.4% 噪音级）；_abck 本轮未下发属目标侧惰性 |
| 传感器 POST + collector 遥测 | PerimeterX/HUMAN（zillow.com 实测；live.com 落地页同厂商） | ✅ 首个 aligned 厂商（19 vs 19 请求多重集一致）；_px3/_pxvid/pxcts 下发与 collector POST 全捕获；live.com `/api/v2/msft` collector POST ×3 带 body 全捕获 |
| 混淆传感器脚本 + reese84 | Incapsula/Imperva（imperva.com 实测） | ✅ minor（0.6%，差异均为 WordPress 插件噪音）；reese84 cookie 签发在案 |
| 行为遥测 beacon | F5/Shape（southwest.com 实测） | ✅ 复现裁决：目标侧行为——RC* 是内容寻址惰性加载模块，请求集合随会话节奏波动（干净侧自身轮间 14 vs 17），缓存协商头零破坏；beacon POST body ×4 已捕获 |
| 挑战 widget | hCaptcha（accounts.hcaptcha.com/demo 实测） | ✅ 复现裁决为插桩副作用且已修复：文档 meta CSP 含 sha256 hash 源，内联改写后哈希不匹配被 CSP 阻止；新增 `csp_blocks_inline_rewrite` 护栏（hash 源文档原样透传记 skipped_csp_hash），修复后 widget 正常初始化 |
| 412 挑战 + 动态再生 VMP | 瑞数 v5/v6（sso.cnipa.gov.cn 实测） | ✅ 被动链路完整：412 全文 → 动态 JS 全文 → 302 → 签名 XHR，initiator 栈直指动态 JS 内 `_$gP … > eval:2`；⚠️ 插桩 diverged——412 正常收到、改写成功（loops=5、parse_failures=0）但目标静默终止挑战，与 Kasada/gsxt 同类「改写敏感」，建议该形态走被动 profile |

## 4. 运行链路监控（加密参数生成链路）

链路分解：**采集点**（读指纹）→ **变换**（VM/加密计算）→ **落地**
（请求字段/cookie/header）。

现有产物：属性级读取事件（采集点全记录）、VM tick 轨迹（变换过程）、
网络全文 + initiator 栈（落地位置与调用点）、同一 wall-clock 时序坐标系。

因果连接（第七/八阶段已建，taint-lite）：变换层编码/加密 API 值事件
（btoa/atob/encodeURIComponent/TextEncoder/TextDecoder/crypto.subtle）
+ 字符串装配片段（fromCharCode）+ loop 状态快照字符串，离线关联器把
请求字段值按 哈希精确/预览前缀/快照全等/片段包含 四级匹配回产生位置，
产出 `{参数, via, API或loop, realm, ts}` 关联表（评分卡第 6 条）。

残留边界（如实登记）：逐字符装配链（`s+=fromCharCode(x)`）与逐字符读取
（charCodeAt/at/codePointAt）均已合并落事件（第十/十二阶段）；仍不可见的
是 VM 内纯算术中间态（码元加减异或等）——撞上时 check 6 记 unknown 而非
fail（抖音 JSVMP 实测即此类）。

## 5. 产物完备性验收标准（每个实验目标的打分清单）

一个会话的产物应能离线回答以下问题，逐条 ✅/❌ 即为该目标的覆盖评分：

1. VM 程序本体全文在产物里吗？（所有 realm、所有通道）
2. VM 执行了多少次循环、每次状态如何？（有截断即 ❌）
3. 派发形态裁决有 observed 级证据吗？
4. opcode/handler 清单完整吗？各自语义等级（observed/inferred/gap）？
5. 加密参数落在哪个请求的哪个字段？有 initiator 栈吗？
6. 该字段的值与哪些环境采集点相关？（当前允许人工关联）
7. redirect 链每个环节的请求/响应字节完整吗？缺的自描述了吗？
8. 原生事件有没有噪音混入、丢失（dropped=0）？
9. 目标有没有用 Worker/iframe/wasm？用了的都覆盖了吗？没覆盖的记 gap 了吗？
   （wasm 子项诚实判定：有 wasm 请求时产物须有 wasm_modules 证据；
   无证据但活性自检通过 → 记 unknown「目标未在观测窗口实例化」，非 fail）
10. 时序上 JS 轨迹与原生事件能对齐吗？

> 验收注意点（第五~八阶段实测沉淀）：被动会话的负载深度天然波动——
> 抖音同类条件四轮实测 364→54 请求、61→3 循环。评分低先要区分
> 「能力缺口」与「本轮目标没跑这段代码」；探针的活性自检（wasm 实例化、
> 值事件）就是为这个区分服务的。

## 6. 实验目标矩阵（候选池，按形态代表性分层）

原则：被动观察纪律（不提交真实凭证、不触发风控动作、随机不存在标识符），
沿用 dola 冒烟的伦理边界；每个目标先跑「覆盖评分清单」，缺口回流为
浏览器能力需求。

| 优先级 | 目标 | 厂商/形态 | 实验价值 |
|---|---|---|---|
| 已验证 | dola→Google 登录 | BotGuard 表间接 VM | 基线回归 |
| 已验证 | 字节系站点（抖音 web） | 自研 JSVMP（集中式派发、X-Bogus/msToken） | 第五~十阶段多轮 |
| 已验证 | canadagoose | Kasada 主动反调试 | 第十一阶段 Worker 收口通过 |
| 已验证 | gsxt.gov.cn | 加速乐（页面世界零容忍） | 第十一阶段定案 + 第十二阶段被动评分 |
| 已验证 | reCAPTCHA v3 演示页 | Google 另一套 VM | 第五阶段 |
| 已验证 | leboncoin.fr | DataDome 传感器 POST | 第十二阶段：被动全量 + payload 捕获；插桩 diverged（平台层限制） |
| 已验证 | turnstile.zeroclover.io | Cloudflare managed challenge | 第十二阶段：被动 8s 通过；插桩 diverged |
| 已验证 | zappos.com | Akamai BMP | 第十二阶段：插桩透明（minor） |
| 已验证 | zillow.com / signup.live.com | PerimeterX/HUMAN | 第十三阶段：首个 aligned；live.com 厂商纠偏（实测是 HUMAN 非 Arkose） |
| 已验证 | imperva.com | Incapsula reese84 | 第十三阶段：minor（WP 插件噪音） |
| 已验证 | southwest.com | F5/Shape | 第十三阶段：复现裁决为目标侧惰性加载波动 |
| 已验证 | accounts.hcaptcha.com/demo | hCaptcha | 第十三阶段：CSP hash 护栏修复后插桩透明 |
| 已验证 | sso.cnipa.gov.cn | 瑞数 v5/v6（412 + 动态再生 VMP） | 第十四阶段：被动链路闭环；插桩 diverged 走被动 profile |

厂商签名（cookie/JS 文件/响应头）对照表已有公开情报支撑。 [Scrapfly](https://scrapfly.io/blog/posts/how-to-bypass-anti-bot-protection "citation"), [DataDome](https://datadome.co/changelog/vm-based-obfuscation/ "citation"), [CSDN 瑞数 6 逆向实录](https://blog.csdn.net/weixin_42384784/article/details/160260899 "citation")

## 7. 浏览器能力扩展路线图（缺口回流，按优先级）

| 优先级 | 能力缺口 | 触发场景 |
|---|---|---|
| ~~P0~~ ✅ | wasm 捕获与插桩（第六阶段完成） | DataDome 类三层目标 |
| ~~P0~~ ✅ | opcode 执行序列 + 参数记录（第九阶段完成；返回值锚 return 为适配器职责） | 所有 VM 的语义钉死 |
| ~~P1~~ ✅ | 外部 script src 的 tick 插桩（第五阶段完成） | 外链 VM 目标 |
| ~~P1~~ ✅ | 值级数据流关联 taint-lite（第七/八阶段完成，边界如实登记） | 加密参数链路自动化 |
| ~~P1~~ ✅ | 结构签名锚点（第十阶段 A 完成：等长归一化 + `_+` 模式，改名免疫） | 动态再生目标（瑞数/Kasada） |
| ~~P2~~ ✅ | 跨域 iframe postMessage drain 桥 + ServiceWorker gap 登记（第十阶段 D 完成） | 特殊目标 |
| ~~P2~~ ✅ | route.fetch loopback urllib 绕行（第十阶段 B 完成） | 本地夹具测试 |
| ~~P2~~ ✅ | 插桩分叉检测闭环（第十阶段 E2：双会话对比脚本） | 强反调试目标（Kasada） |
| ~~P2~~ ✅ | 流式落盘（第十阶段 E1：>32MB 拆旁车 NDJSON） | 超大 VM |
| ~~P0~~ ✅ | Worker route 层插桩 + 直通 Worker 注册表（第十一阶段） | Kasada/Shopify 类 Worker 盲区 |
| ~~P1~~ ✅ | toString 伪装守护全家桶（第十一阶段：WeakMap 登记 + 引擎真实自描述串 + 原位描述符替换 + 去 AsyncFunction） | 检查包装函数 toString 的目标 |
| ~~P0~~ ✅ | 引擎层 initiator 栈（第十二阶段：http-on-opening-request 同步抓 Components.stack，ppmm 送父进程按 channelId 并入请求事件，driver 三处补丁透出，零页面世界污染实测对照逐字节一致） | 零容忍目标的调用点证据（被动模式 initiator 全 null 缺口） |
| ~~P1~~ ✅ | 在途/中止请求终态自描述（第十二阶段：terminal_state/failure_reason/pending_at_close） | DataDome 传感器 POST 中止类证据 |
| ~~P1~~ ✅ | wasm Instance 构造器 + charCodeAt 读取合并 + rec_ret 返回值锚定（第十二阶段） | 语义钉死收尾 |
| ~~P2~~ ✅ | divergence 口径 schema 2（token 归一化 + POST body 扫描）+ parse_failures 拆分（syntax/nonscript） | 裁决误判消除 |
| ~~P1~~ ✅ | 引擎体捕获透出（第十三阶段：核查发现 juggler stock 已具备 readRequestPostData/ResponseStorage，真实缺口全在 MCP 透出层；engine_request_body/engine_response_body base64 + size/truncated 自描述，omni.ja 零改动；零污染对照逐字节一致） | 二进制请求体/响应体无损证据（旧链路 utf-8 静默丢弃） |
| 观察项 | 引擎级隐形 hook 的其余面——initiator 栈 + 体捕获两例后引擎层先例已充分；route 重发缺 Priority 头属平台层限制（Juggler 丢弃，route 层补不齐）；juggler 请求体 10MB 上限超限静默缺席（无标记）；SSE/streaming 需 onStopRequest 完成才可得 | 加速乐类零容忍目标；DataDome 插桩陈旧预渲染连锁 |
| 待复现 | ~~第十三阶段 P2 逼出三例插桩侧分歧~~ **已全部裁决（第十三阶段复现）**：hCaptcha widget 阻断 = CSP hash 源文档内联改写被阻止 → `csp_blocks_inline_rewrite` 护栏修复；live.com 302 链消失 = route.fetch 默认跟随重定向吞掉 document 302 链 → `max_redirects=0` + 3xx 原样透传修复；F5/Shape RC* chunk 波动 = 目标侧内容寻址惰性加载，如实登记。divergence 子域 token 归一化已补（normalize_host，功能子域有测试锁定不误伤） | ~~MCP stdio 间歇断管~~ 第十四阶段已修 |
| ~~工具链~~ ✅ | MCP stdio 间歇断管根因修复（第十四阶段）：根因是并发工作线的广谱 pkill 误杀刚 spawn 的服务器（EPIPE/EOF 逐字复现）；客户端 `start_retries=2` 握手期透明重生（会话期绝不重试）+ `scripts/mcp-cleanup.sh` scoped 清理纪律；修复后注入杀死 20/20 = 0% 失败 | 多线并行实测稳定性 |
| ~~P2~~ ✅ | divergence 加密参数 hints 覆盖三段点分签名形态（第十四阶段：`_DOTTED_SIG_RE` 兜底，瑞数 BOJRPc7LzNOPP 类 cookie 签名可归一） | 瑞数类动态签名参数虚差消除 |
| 登记项 | 瑞数/网宿系对 route 文本改写敏感（插桩 diverged，走被动 profile）；divergence 会话 tick 级 trace 未持久化；瑞数目标池 2026 迁移（wenshu/sse/csrc 首页无挑战，wcjs 主站已换网宿系）；`list_network_requests` size 字段封顶 200,000 易误读为截断（raw body 实际完整） | 改写敏感目标的被动通道；目标池情报维护 |

路线图外三条边界（第十阶段一并清尾）：wasm exports 调用计数 ✅（C1
遮蔽方案）；逐字符装配链关联 ✅（C2 合并缓冲，64 字符粒度）；惰性
wasm 触发 ✅——判定为目标侧行为而非浏览器缺口，用现有
`--click-expression` 触发即可闭环，无需浏览器改动。

## 8. 与本项目既有文档的关系

- 设计基线：[2026-09-23-reverse-analysis-browser-design](superpowers/specs/2026-09-23-reverse-analysis-browser-design.md)
- 实战验证：第二/三/四阶段报告（proof 链、VM 结构还原、产出物完备性）
- 能力爬坡：[第五阶段多目标](reverse8-phase5-multi-target-2026-09-25.md)、
  [第六阶段 wasm](reverse8-phase6-wasm-coverage-2026-09-25.md)、
  [第七阶段 taint-lite](reverse8-phase7-taint-lite-2026-09-25.md)、
  [第八阶段字符串原语](reverse8-phase8-string-taint-2026-09-25.md)、
  [第九阶段 opcode 序列](reverse8-phase9-opcode-seq-2026-09-25.md)、
  [第十阶段清尾（P1/P2 + 三边界）](reverse8-phase10-cleanup-2026-09-25.md)、
  [第十一阶段 Worker 收口 + 插桩隐蔽性边界](reverse8-phase11-stealth-boundary-2026-09-25.md)、
  [第十二阶段缺口闭合](reverse8-phase12-gap-closure-2026-09-26.md)（含
  [P1 厂商实测](reverse8-phase12-p1-probes-2026-09-26.md)、
  [gsxt 被动评分](reverse8-phase12-gsxt-passive-scorecard-2026-09-26.md)、
  [initiator 栈落地](reverse8-phase12-initiator-stack-feasibility-2026-09-26.md)）
- 第十三阶段：[P2 厂商补测](reverse8-phase13-p2-probes-2026-09-26.md)（五家 +
  八厂商形态矩阵）、[引擎体捕获收口](reverse8-phase13-engine-body-capture-2026-09-26.md)、
  [分歧复现裁决](reverse8-phase13-repro-adjudication-2026-09-26.md)（CSP hash 护栏 +
  document 302 链直通修复）
- 第十四阶段：[瑞数 P0 全链路实测](reverse8-phase14-ruishu-p0-2026-09-26.md)
  （sso.cnipa.gov.cn，412 + 动态再生 VMP 形态闭环）、
  [MCP stdio 断管根因](reverse8-phase14-stdio-rootcause-2026-09-26.md)
  （pkill 误杀根因 + 客户端透明重生修复）
- 第十五阶段：[release 直装管线](reverse8-phase15-release-pipeline-2026-09-26.md)
  （GHA tag 触发修复 + agent 自举包资产 + README/skill 改 release 直装流程）
- 本文档是能力定义层：后续每个实验目标按第 5 节清单打分，
  缺口按第 7 节优先级回流为浏览器改动。
