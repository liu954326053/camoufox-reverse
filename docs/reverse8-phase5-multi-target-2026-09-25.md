# 第五阶段：多目标 VM 实验矩阵（能力爬坡第一轮）

日期：2026-09-25 ｜ 计划：[2026-09-25-multi-target-vm-experiments-plan](superpowers/plans/2026-09-25-multi-target-vm-experiments-plan.md) ｜ 能力模型：[vm-reverse-capability-model-2026-09-25](vm-reverse-capability-model-2026-09-25.md)

方法：新增**通用目标探针**（`scripts/reverse-browser-vm-target-probe.py`，
目标无关），对每个目标被动加载、drain 全 realm 轨迹、自动生成
10 条覆盖评分卡（`derived/coverage-scorecard.json`）。缺口当场回流为
浏览器能力改动，复测验证后打分。

## 一、三个目标的评分与结论

| 目标 | VM 形态 | 评分 | 关键结果 |
|---|---|---|---|
| Google BotGuard（dola→登录） | 表间接派发 | 基线（四阶段） | proof observed ×多轮 |
| 抖音 web | 集中式 JSVMP + wasm | **6/10** | 42 循环 / 335 841 迭代 / 0 截断 / 97 源码全文 |
| reCAPTCHA demo | Worker + importScripts 载荷 | **6/10** | 50 循环 / 86 966 迭代 / 0 截断 / 50 带时序 |
| 瑞数三站点 | 动态再生 + VMP | **边缘拦截** | JS 零执行，见下文 |

评分卡剩余扣分项全部是已登记的已知缺口（wasm 盲区、taint-lite
值级关联、目标适配器待写），无新增不明缺口。

## 二、本阶段逼出并修复的浏览器能力缺口（5 项）

全部遵循分层纪律：通用能力进 hook/工具层，不认识任何目标。

| # | 缺口 | 触发目标 | 修复 |
|---|---|---|---|
| 1 | **外部 script src 不插桩**（route 只改写 HTML） | 抖音（安全 SDK 全走外链） | route 层 `rewrite_js_body`：JS content-type / .js 后缀双通道，`inject_loop_ticks` 扩展 `while(1)` 与 `for(;;)` 形态 |
| 2 | **tick 运行时与 route 层签名不兼容**（潜伏 bug：Python 语句形态 `tick(id, snapshot)` 会让运行时 `condFn()` 直接 TypeError 崩页面，此前从未被真实触发） | 抖音修复时挖出 | 运行时 tick 双签名兼容 |
| 3 | **data: URL Worker 不插桩** | 抖音（SDK 嵌套 Worker 用 data: URL） | 解码（base64/percent）后正常插桩 |
| 4 | **importScripts 通道未插桩** | reCAPTCHA（Worker 入口仅 102 字节 loader，主力 848KB 经 importScripts 落地） | Worker 内同步 XHR 取源码→插桩→blob 重打包；取不到记 `importscripts-gap` |
| 5 | **frame 内 Worker 的 drain 聚合缺失**（注册表在 frame 侧）+ **for 头分号解析 bug**（`for(R=function(){for(;;){}},…;;)` 内层分号误当外层分隔符，产出非法 JS，848KB 载荷整体语法错误零执行） | reCAPTCHA | drainAllAsync 递归调 frame 的 drainAllAsync（`frame[i]/worker[j]` 前缀、frame top 去重）；扫描器 for 头加分号大括号深度约束，嵌语句块的外层保守跳过、内层正常命中 |

验证：真实 848KB 载荷修复前语法错误 → 修复后 458 循环注入、
esprima/node 双重语法校验通过；MCP 119、node 离线 71 项全绿。

## 三、瑞数形态结论：第 0 层是边缘通过性

三个公开确认的瑞数目标（jzsc.mohurd.gov.cn TLS 证书错误页、
zxgk.court.gov.cn 403、gsxt.gov.cn 403「请求异常」响应体全文已捕获）
全部在 **JS 下发之前**被边缘网关拒绝——Botgate 的 TLS 指纹/IP 信誉
检查先于一切客户端代码。**对瑞数形态，瓶颈不在 VM 插桩而在边缘
通过性**（指纹一致性/出口 IP 信誉），已登记为能力模型的第 0 层前提，
与 VM 证据采集正交。

## 四、探针工程改进（目标无关，沉淀为通用能力）

- `--wait-until domcontentloaded`（重站点 load 事件永不触发）；
- 分段等待容忍导航中断（执行上下文销毁不再中断探测）；
- `--click-expression` 无害交互触发（reCAPTCHA 复选框类目标必需）；
- 评分卡修正：零长度事件标记不计入源码全文分母；超 256KB 源码与
  raw/scripts 按 hook 同款哈希交叉命中即视为离线可用；wasm 请求
  出现即 realm 覆盖诚实扣分。

## 五、已知边界更新（下一阶段需求池）

| 优先级 | 缺口 | 实测依据 |
|---|---|---|
| P0 | **wasm 捕获与插桩** | 抖音 `pylon-wasm/index_v2.wasm` 实锤加载，内部全盲 |
| P0 | opcode 执行序列 + 参数/返回值 | 频次已有，语义钉死差这层 |
| P1 | 值级数据流关联（taint-lite） | 两目标均 unknown |
| P1 | 边缘通过性（TLS 指纹/IP 信誉） | 瑞数三站点边缘拦截 |
| P2 | 加密参数签名扫描含 POST body（reCAPTCHA token 走 body 不走 URL） | reCAPTCHA 5/10 项 unknown |
| P2 | 字节系/reCAPTCHA 目标适配器（派发裁决、opcode 清单） | 评分卡 3/4 项 needs-adapter |

## 六、测试状态

MCP 119 passed ／ node 离线 71 项 ALL PASS ／ pythonlib 363 ／
adapters 48（第四阶段基线）。实战：抖音、reCAPTCHA、瑞数三站点
共 8 轮探针会话，产物全部落 `artifacts/analysis/phase5/`。
git 未主动提交。
