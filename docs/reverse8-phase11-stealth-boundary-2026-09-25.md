# 第十一阶段：Worker 盲区收口 + 插桩隐蔽性边界实测（gsxt 加速乐定案）

日期：2026-09-25 ｜ 状态：完成 ｜ 实战目标：canadagoose（Kasada）、gsxt.gov.cn（加速乐）

## 0. 一句话结论

Kasada 线的 Worker 覆盖缺口已修复并实测通过；gsxt 加速乐线经七轮二分定案为
**「页面世界函数级替换的隐形性存在 JS 层不可逾越的边界」**——纯替换
`Function.prototype.toString`（零全局变量、零其他改动）即被挑战侦测并静默卡死，
Proxy 方案会把 SpiderMonkey 主线程打死。对该类目标，正确能力答案是
**被动捕获 profile + 插桩分叉检测裁决**，而非更深的 JS 伪装。

## 1. 修复清单（全部含实测证据）

### 1.1 Worker realm 覆盖收口（Kasada 线，P0 缺口）
- **route 层 Worker 插桩**：Worker 脚本响应（`resource_type=worker` 或
  `sec-fetch-dest: worker/sharedworker`）在 tick 改写后前置完整运行时——
  Worker realm 没有 init script 安装的运行时，页面内同步 XHR 取源码会被
  CSP connect-src 拦（Shopify web-pixels 实测），route 层是唯一可靠入口。
  （`tools/vm_loop.py` route_handler）
- **直通 Worker 入注册表**：拿不到源码的 Worker（跨域/构造抛错）也登记进
  drain 注册表，drainAllAsync 出现超时条目而非静默遗漏（node 契约 case 31/35
  收口：worker realm 条目数 = 3）。
- **worker-gap 缺口的 context.request 取回链**修复，canadagoose（Kasada）
  实测通过。

### 1.2 route.fetch 后 continue_ 双发请求隐患（gsxt 卡死的第一个根因）
route.fetch() 之后再 continue_ 会让同一请求**物理发两次**；令牌类挑战页
（521 + 动态令牌）第二次命中直接返回错误页。修复：不改写的响应一律用已取回
的响应 fulfill 直通（`vm_loop.py` route_handler 注释在案）。

### 1.3 MCP 静默代理默认值（gsxt 403 根因）
MCP server 启动参数不显式传 `--proxy` 时静默走本机 7890 代理，gsxt 直连场景
被代理拦截返回 403。已改为显式语义；矩阵脚本统一显式 `--proxy ""`。

### 1.4 drain 孤立代理项编码崩溃
drainAllAsync 聚合时孤立的代理 realm 条目（无 data）触发编码异常，已修复
如实降级为 error 条目。

### 1.5 toString 伪装守护强化（fetch_hook / xhr_hook / vm_loop_trace）
- WeakMap 登记「包装函数 → 原生源码串」，守护版 `Function.prototype.toString`
  命中即返回原生串（自有 toString 属性会被 `FP.toString.call` 绕过，必须守护
  原型方法本身）。
- name/length 与原函数对齐；普通函数自带的 `prototype` 自有属性（不可配置
  删不掉）值对齐 undefined。
- 重复安装检测改为查 WeakMap，不再挂 `__mcp_guard` 自有标记属性
  （getOwnPropertyNames 枚举面）。
- **守护自描述串取自引擎真实输出** `_FPT.call(_FPT)`：Firefox 原生格式是
  多行缩进（`function toString() {\n    [native code]\n}`），与 Chrome 单行
  格式不同，此前硬编码 Chrome 格式在 Firefox 上即为明显指纹。
- fetch_hook 额外两处隐形化：
  - `async function` 包装改普通函数 + then 链（async 的 constructor 是
    AsyncFunction、`Object.prototype.toString` 输出 `[object AsyncFunction]`，
    与原生 fetch 不符）；
  - `replaceOnOwner`：在原生定义处（Window.prototype / Navigator.prototype）
    克隆原描述符原位替换，不再给 window/navigator 实例加自有属性
    （`hasOwnProperty('fetch')` 检测面）。

## 2. gsxt 卡死二分定案（七轮实验）

| 实验 | 配置 | 结果 |
|---|---|---|
| 基线 | 无钩子被动捕获 | ✅ 9s 过挑战（521→412→200，46 请求） |
| error_trap | 仅错误捕获 | ✅ 过 |
| fetch no-guard | fetch 包装、无 toString 守护 | ❌ 挑战提交被 405/400 拒（包装被识别） |
| fetch + guard | 包装 + 守护 | ❌ 卡 "Environment Checking"，零遥测 |
| no-replace | 只装守护不替换 fetch | ❌ 同样卡死 |
| guard-only | 守护 + 无 beacon/日志 | ❌ 同样卡死 |
| **pure-guard** | **仅替换 FPT，零全局变量、零其他改动** | ❌ **同样卡死** |

辅助证据：
- 反射 API 探针（getOwnPropertyNames / getOwnPropertyDescriptor /
  Reflect.ownKeys / hasOwnProperty，带调用栈）全程 **0 次调用**——侦测不走
  反射 API，走语法级通道（`new FPT()` 可构造性、`'prototype' in FPT` 这类
  无法 hook 的操作符）。
- Proxy 版守护（理论上 name/length/prototype/ownKeys/construct 全透传原生）
  两次实测均把 SpiderMonkey 主线程打死（evaluate 全超时，重入哨兵无效）——
  该引擎上 Proxy 包裹 `Function.prototype.toString` 不可行。
- vm_loop 关守护全量插桩同样卡死——加速乐对页面世界**任何**函数级替换
  零容忍。

**结论**：gsxt（加速乐）类目标的二阶段环境检测能在不调用任何反射 API 的
情况下识别「`Function.prototype.toString` 非原生」。纯 JS 层不存在完全隐形的
替换方案（普通函数可 `new`/有 prototype 自有属性；箭头/bound 丢失动态 this；
async 改 constructor 链且返回 Promise；Proxy 打死引擎）。

## 3. 对该类目标的正确能力形态

1. **被动捕获 profile**：`capture_profile=raw` + 不装 hooks/vm_loop，基线
   实测 9s 过挑战，网络全文证据完整（请求/响应 body、redirect 链）。
2. **插桩分叉检测做裁判**：`scripts/reverse-browser-instrumentation-divergence.py`
   双会话实测裁决 gsxt = **diverged**（请求数差 90.4%，被动侧 52 请求 vs
   插桩侧 5 请求），报告落盘
   `artifacts/analysis/phase11/gsxt-divergence/validation/963143421211481886da7f76995c3c75/instrumentation-divergence.json`。
   分叉检测器正是「插桩是否改变目标行为」的诚实裁判——本阶段它正确给出了
   diverged，能力闭环。
3. 引擎级（C++/Juggler 原生层）隐形 hook 是理论上唯一可行方向，工作量大，
   暂列观察项而非路线图承诺。

## 4. 验证

- node 契约：`verify_vm_loop_inject.node.js` 全绿（含 17b toString 守护四项、
  worker 注册表收口用例）——恢复正式实现后复跑 ALL PASS。
- 集成套件：`integrations/camoufox-reverse-mcp` 124 passed, 2 skipped。
- pythonlib：363 passed, 5 skipped。
- 仓库级逆向测试：57 passed；6 个失败为环境缺失（裸 `python3` 子进程缺
  orjson/pixelmatch），与本次改动无关（改动只触三个 hook JS 文件 +
  vm_loop.py）。
- gsxt 基线（被动）：PASS at ~9s。

## 5. 遗留与边界（如实登记）

- gsxt 挑战二阶段在**任何**页面世界插桩下不可达——产物里以 diverged 裁决 +
  被动证据链呈现，不伪造插桩覆盖。
- 守护对「容忍 FPT 替换但检查包装函数 toString」的目标（此前 405 死循环
  场景）仍然有效且必要；边界只在「连 FPT 本身都验」的目标。
- Proxy 守护在 SpiderMonkey 的楔死机理（疑似引擎原生 toString 对 Proxy
  求值重入）未完全定位，如需启用需引擎层诊断。
