# Camoufox Reverse

Camoufox Reverse 是一个面向 Web 协议、混淆脚本、动态代码和 JavaScript 虚拟机分析的浏览器基础平台。它在 Camoufox 的 Gecko 层提供可选的 PropertyTracer，并把网络、脚本、Cookie、Storage、环境访问和执行事件保存到指定工程目录，供后续分析器建立调用链和值生成链。

本项目只提供逆向分析基础设施，不把任何具体站点逻辑写死在浏览器核心中。Google 登录、验证码、BotGuard 或其它第三方认证流程不会由本项目自动填写账号、绕过防护或收集凭据；真实目标分析必须在获得授权的前提下由使用者主动操作。

## 当前状态

已实现并验证的基础能力：

* 必填的 `project_dir` 和工程目录权限校验；

* 每次启动创建独立 `session_id`，显式 `resume_session` 才能恢复未完成 session；

* 原始网络、脚本、状态、截图和 trace 追加写入 session 的 `raw/` 或 `trace/`；

* raw 文件索引重建、事件丢失计数和异常退出后的 `incomplete` 状态；

* Python 启动 API、`reverse-browser` CLI 和独立 MCP 适配器共用同一套工程/session 核心；

* MCP/CLI 的路径 containment、关闭幂等、错误 envelope 和敏感值不回显；

* 随项目提供的中文 Agent Skill 与 Python MCP stdio client。

经十四个阶段实战爬坡（14 家厂商/形态实测），VM 逆向证据采集能力已闭环：

* VM 循环级轨迹（tick 计数 + 状态快照 + 覆盖率自描述）、函数级追踪（注册/派发计数、有序执行序列、参数/返回值锚定）；

* 动态代码全通道捕获（eval/Function/TrustedTypes/script 元素/Worker，≤256KB 全文）；Worker/iframe/跨域 iframe 各 realm 聚合；

* wasm 五入口插桩（compile/instantiate/compileStreaming/instantiateStreaming/Instance 构造器），字节哈希 + ≤768KB base64 + imports/exports 调用计数；

* 值级数据流关联（taint-lite）：编码/加密 API 值事件 + 字符串装配/读取合并事件 + 四级离线匹配；

* 网络证据：请求/响应全文、引擎层 initiator 调用栈（零页面世界污染）、引擎体捕获（二进制 base64 无损 + size/truncated 自描述）、redirect 链与在途/中止请求终态自描述；

* 插桩分叉检测（divergence）：被动/插桩双会话对比，aligned/minor/diverged/gap 裁决。

已登记的结构性边界（原理限制，非待办）：页面世界零容忍目标（如加速乐）JS 层插桩不可隐形，走被动通道；ServiceWorker realm 引擎不可达；瑞数/Kasada 类改写敏感目标插桩即被侦测，建议被动 profile。完整能力定义见 [能力模型](docs/vm-reverse-capability-model-2026-09-25.md)。

通过基础测试不等于某个第三方站点登录成功；真实目标分析必须在获得授权的前提下由使用者主动操作。

## 目录结构

```text
project/
├── project.json
├── runs/
│   └── <session-id>/
│       ├── manifest.json
│       ├── raw/
│       ├── trace/
│       ├── derived/
│       └── report/
└── indexes/
```

`raw/` 和 `trace/` 保存完整原始数据，不做脱敏、截断或有损转换；`derived/`、`report/` 和 `indexes/` 只保存索引与派生结果。工程目录应视为敏感数据目录，不要把它提交到 Git 或上传到第三方服务。

## 安装 Python 核心

要求 Python 3.10 或更高版本。纯 browser-free 测试不需要启动浏览器：

```bash
cd pythonlib
python3 -m pip install -e .
```

安装后会提供 `reverse-browser` 命令。普通 Camoufox 的已有安装和 active 版本不会被自动切换。

### 安装 PropertyTracer 浏览器

需要原生 trace 时，安装与当前 Python 包匹配的固定 reverse 构建。当前 selector 是：

```text
whitenightshadow/152.0.4-beta.30-reverse.9
```

当前本地已构建并验证的是 macOS arm64 的 `reverse.9`，这不表示 GitHub 上已有同版本预构建包。可以使用下面的脚本本地构建；若使用发布归档，必须确认 selector、平台和 SHA-256 与目标版本一致。不要把 reverse 构建解压到普通缓存目录，也不要覆盖已有 active 版本。安装完成后可检查：

```bash
python3 -m camoufox list
python3 -m camoufox active
```

普通 Camoufox 和 reverse 构建可以并存。reverse runtime 默认选择上述固定构建；只有显式传入 `--browser-version` 时才会覆盖它。

### 本地构建与打包

`scripts/build-reverse-browser.sh` 固定构建 macOS arm64 的上述版本，生成 ZIP、SHA-256 文件并校验版本及能力标记。脚本支持从任意工作目录调用；相对输出路径按调用时的工作目录解析。

```bash
# 在仓库根目录执行；--plan 不下载、不编译。
bash scripts/build-reverse-browser.sh --plan
bash scripts/build-reverse-browser.sh --check-only

export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
bash scripts/build-reverse-browser.sh --output-dir dist-reverse.9 --install
```

首次构建需要 Xcode/SDK、Python 3、Rust、make、clang、xz、7z 和 aria2c 或 curl，建议至少保留 30 GiB 可用空间。Mozilla 工具链默认隔离在仓库的 `.mozbuild-reverse`，可用 `--mozbuild-state PATH` 指定。`--check-only` 检查命令和版本契约并报告剩余磁盘，不代表已经编译验证 SDK，也不代替实际构建的磁盘门槛检查。

完整源码包通过 `xz -t` 后复用；部分下载尝试续传，再检查压缩流完整性。已有 `_READY` 源码树会检查 `configure.py`、77 个原生 hook、当前 tracer/network additions 及快速退出落盘补丁后继续编译，不会重复打补丁。缺少 `_READY` 或实现过期的现存源码目录会保留并报错，需明确移走或同步更新后再重跑。默认保留构建源码；只有显式传入 `--no-keep-source` 才在成功后删除生成的源码树。

2026-09-25 的本地构建、受控 smoke 和 Dola → Google 实测结果见 [验收记录](docs/reverse6-build-smoke-2026-09-25.md)。真实站点采集存在 `incomplete` 情况；原生属性事件不能等同于完整 JS 调用栈或值生成链。

## CLI 使用

启动时 `--project-dir` 必填。按本机约定使用 `127.0.0.1:7890` 代理时：

```bash
reverse-browser launch \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --browser-version whitenightshadow/152.0.4-beta.30-reverse.9 \
  --trace-profile targeted \
  --headless \
  --url about:blank \
  --duration 5
```

不需要原生 trace 时可以加 `--no-trace`。不提供 `--duration` 时，命令会持续运行，收到 `SIGINT` 或 `SIGTERM` 后关闭 session。

查询 session、重建索引和生成报告：

```bash
reverse-browser session list \
  --project-dir /absolute/path/to/project

reverse-browser trace index \
  --project-dir /absolute/path/to/project \
  --session <session-id>

reverse-browser report build \
  --project-dir /absolute/path/to/project \
  --session <session-id>
```

CLI 输出为 JSON。错误输出不会回显代理密码、Cookie、token 或异常原文中的敏感值。

## MCP 服务器

MCP 服务器源码已随本项目放在 `integrations/camoufox-reverse-mcp/`，与浏览器版本和 Python 核心一起交付。推荐使用项目内启动脚本，它会自动加载本项目的 `pythonlib/` 和 MCP `src/`：

```bash
bash scripts/run-reverse-mcp.sh \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --browser-version whitenightshadow/152.0.4-beta.30-reverse.9 \
  --headless
```

`project_dir` 必填，默认代理为 `http://127.0.0.1:7890`。MCP server 的外部依赖仍需在 Python 环境中安装，但不再依赖隐藏的 `.codex/mcp-servers/` 路径。

MCP 的 `launch_browser` 要求 `project_dir`，默认捕获模式是 `raw`。核心工具包括浏览器启停、页面导航、网络请求、脚本保存、状态导出、PropertyTracer、动态脚本 instrumentation 和 session 文件查询。所有返回的 artifact 路径都必须位于当前 session 目录内。

MCP 契约见 [`docs/reverse-browser-mcp-contract.md`](docs/reverse-browser-mcp-contract.md)。MCP client 位于项目根目录的 `mcp/`，只使用 Python 标准库，通过 MCP stdio JSON Lines 协议连接服务器，不依赖 Node、浏览器或 JavaScript：

```bash
cd mcp
python3 -m pip install -e .
python3 -m camoufox_reverse_mcp_client \
  --command ../scripts/run-reverse-mcp.sh \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  list-tools
```

也可以直接通过内置 client 启动项目内 server：

```bash
bash mcp/run-client.sh \
  --project-dir /absolute/path/to/project \
  list-tools
```

## Agent Skill

中文 Skill 位于项目根目录的 `skill/SKILL.md`。它规定了工程目录、session 隔离、原始证据、MCP/CLI 调用和安全边界，可复制到 `$CODEX_HOME/skills/camoufox-reverse-browser/` 或其它兼容 Agent 的 skill 目录。安装脚本默认只复制到指定 skill 目录，不修改宿主全局 MCP 配置：

```bash
python3 scripts/install_reverse_browser_agent.py \
  --project-dir /absolute/path/to/project \
  --apply
```

脚本默认是 dry-run，不创建目标目录。需要生成 MCP 配置示例时显式指定输出路径并使用 `--apply`：

```bash
python3 scripts/install_reverse_browser_agent.py \
  --project-dir /absolute/path/to/project \
  --mcp-config /absolute/path/to/project/mcp-config.json \
  --apply
```

配置示例只包含命令、工程目录和代理地址，不包含密码、Cookie 或 token。脚本不会自动启动浏览器，不会访问网络，也不会覆盖已有配置，除非显式使用 `--force`。

### 发给 Agent 的自动安装消息（零仓库自举版）

下面这段消息可以发给**任何一台机器上**支持本地命令执行的 Agent——不要求它本地已经有本仓库。浏览器二进制、Python 包、Skill 和 MCP 配置全部从 GitHub Release 的 `reverse.9` 资产获取，每一步都幂等且可验证：

```text
请在这台机器上安装 Camoufox Reverse 逆向分析浏览器（release 直装，不需要 clone 任何仓库）。严格按顺序执行，任何一步失败就停下来报告，不要跳过：

1. 下载自举包并校验 SHA-256（reverse.9）：
   curl -fLO https://github.com/liu954326053/camoufox-reverse/releases/download/v152.0.4-beta.30-reverse.9/camoufox-reverse-agent-reverse.9.tar.gz
   curl -fLO https://github.com/liu954326053/camoufox-reverse/releases/download/v152.0.4-beta.30-reverse.9/SHA256SUMS
   从 SHA256SUMS 取出 camoufox-reverse-agent-reverse.9.tar.gz 的期望哈希，用
   shasum -a 256（或 sha256sum）核对，不一致立即终止并报告。
   需要代理时设置 https_proxy 环境变量，不要把代理地址硬编码进脚本或配置。
   校验通过后解压：mkdir -p ~/camoufox-reverse-agent &&
   tar -xzf camoufox-reverse-agent-reverse.9.tar.gz -C ~/camoufox-reverse-agent

2. 运行自举安装（要求 Python ≥ 3.10；脚本幂等，失败修复后重跑即可）：
   cd ~/camoufox-reverse-agent && bash install.sh
   它会依次完成：pip 安装 pythonlib / integrations/camoufox-reverse-mcp / mcp
   三个包（只装这三个，不要安装 Node、Selenium 或其它自动化框架）→
   按当前平台从同一 release 下载浏览器二进制并强制 SHA-256 校验安装
   （selector 固定 whitenightshadow/152.0.4-beta.30-reverse.9；首次使用会先
   装一个官方 Camoufox 初始化 0.5 缓存作为 active 保底，不会改装其它版本）→
   安装 Skill → 在工程目录生成 mcp-config.json 示例 → list-tools
   browser-free 自检。工程目录默认 ~/camoufox-reverse-evidence，
   可用 bash install.sh /绝对路径/工程目录 指定。

3. 把第 2 步生成的 mcp-config.json 里的 camoufox-reverse 条目追加进你的
   MCP 配置（只追加，不覆盖已有条目；如宿主不支持 MCP，说明情况即可）。

4. 只做 browser-free 校验，然后报告：
   - 第 2 步末尾的 list-tools 自检已返回工具清单；
   - 输出中没有密码、Cookie、token；
   - 报告实际修改/新建的文件路径和每条校验命令的结果。
   不要启动浏览器访问第三方网站，不要登录、不要处理验证码、不要触发风控。
   证据工程目录包含敏感数据，不要加入 Git，不要上传。
```

设计要点：第 1 步只依赖 GitHub Release 的两个资产（自举包 + SHA256SUMS），自举包内目录布局与仓库一致，因此 skill 安装脚本和 MCP client 里基于 `__file__` 的相对路径推导在解压副本里照常成立；第 2 步的浏览器二进制由包内 `scripts/install-camoufox-reverse.py` 强制 SHA-256 与 reverse.9 能力契约双重校验（资产名不含 reverse 后缀，版本区分靠 release tag），下载不到或哈希不匹配会如实失败，不会偷偷装错版本；`install.sh` 保持幂等（已装浏览器跳过下载、`--force` 覆盖 skill 与配置示例），GitHub 下载尊重 `http_proxy`/`https_proxy` 环境变量而不硬编码代理；全程不接触宿主机全局配置（第 3 步只追加单条 MCP 条目）。

## 产物使用手册

本节面向拿到 session 产物的逆向分析者。核心原则：**浏览器是证据采集基础设施，不是逆向工具本身**——分析者只看产物、不重开浏览器，就能把目标的加密逻辑分析到可复现的程度。产物里没有的是能力缺口；产物里有但没说清可信度的也是缺口（所有截断、缺口、失真都有自描述字段）。

### 一、一个 session 里有什么

```text
runs/<session-id>/
├── manifest.json                  # session 元数据：状态、时间、浏览器版本
├── raw/
│   ├── network/<request-id>/
│   │   ├── request.json           # 请求头 + 引擎体捕获（engine_request_body）
│   │   ├── response.json          # 响应头 + 引擎体捕获（engine_response_body）
│   │   └── metadata.json          # 终态自描述 + initiator 栈（见下）
│   ├── network/capture/<id>.jsonl # 捕获会话索引流
│   ├── mcp-network/requests/      # MCP 层网络证据（含引擎 body 字段）
│   ├── vm-loop/<id>.json          # vm_loop_trace drain 主产物（见下）
│   ├── vm-loop/<id>-states.ndjson # payload >32MB 时的旁车状态流
│   ├── worker-source/<id>.js      # Worker 源码回补（worker_recovery）
│   └── .snapshots/                # 页面快照
├── trace/                         # PropertyTracer 原生属性事件
├── derived/  report/  indexes/    # 派生索引与报告（非原始证据）
└── runtime/                       # 运行时临时文件（非证据）
```

`raw/` 保存完整原始数据，不做脱敏、截断或有损转换；少数平台层上限（wasm ≤768KB、引擎体 MCP 层 200KB 截断、juggler 请求体 10MB）都有 `truncated`/`bytes_unavailable`/计数器自描述。

### 二、关键字段速查

**网络 metadata**：`terminal_state` / `failure_reason` / `pending_at_close`（在途/中止请求的终态归因，None 状态不存在）；`engine_initiator_stack`（引擎层调用栈，零页面世界污染，导航类请求记 null 是自描述不是丢失）；`engine_request_body` / `engine_response_body`（base64 + size + truncated，二进制无损）。

**vm-loop 主 JSON**：`loops[]`（tick 计数、状态快照、`coverage_pct` 覆盖率自描述，None 表示纯计数循环 n/a）；`realms`（主页面/iframe/Worker 清单）；`dynamic_sources`（eval/Function/Worker 各通道源码全文，含 `worker-gap`/`serviceworker-gap` 登记）；`wasm_modules`（字节哈希 + imports/exports 计数）；`value_taps`（编码/加密 API 值事件 + 字符串装配/读取合并事件）；`trace_seq`（`__mcp_vm_rec` 有序执行序列 + 参数证据，`__mcp_vm_rec_ret` 锚定返回值）；`counters`（handler 注册/派发频次）；`overhead`（插桩开销自描述）。

**route 统计自描述**：`js_rewritten` / `parse_failures_syntax` / `parse_failures_nonscript` / `skipped_csp_hash`（CSP hash 文档原样透传）/ `redirects_passthrough` / `route_fetch_fallback`（loopback 绕行）。

### 三、标准分析顺序（对应逆向者五步）

1. **定位 VM**：`dynamic_sources` 源码全文 + `realms` 清单 → 哪段代码是 VM、在哪个 realm、何时执行。
2. **还原结构**：`loops[]` 角色分类 + `counters` + `trace_seq` → 派发形态裁决（集中式 while+switch / 表间接 / FSM）。
3. **钉语义**：`trace_seq` 参数证据 + `rec_ret` 返回值 + 原生属性事件（`trace/`）因果关联 → 每个 handler/opcode 干什么。
4. **追数据流**：`value_taps` 四级匹配（哈希精确/预览前缀/快照全等/片段包含）回产生位置 → 加密参数从哪个采集点、经哪些变换、落到哪个请求字段（`engine_initiator_stack` 给调用点）。
5. **验证理解**：`raw/network/` 全文（含 redirect 链每跳字节）+ tick\_times 与原生事件同一 wall-clock 坐标系 → 离线重放所需字节是否齐全。

### 四、十条验收清单（每个目标打完分再下结论）

逐条 ✅/❌/unknown/gap 自评（完整定义见 [能力模型](docs/vm-reverse-capability-model-2026-09-25.md) §5）：① VM 程序全文在案（所有 realm 所有通道）② 循环次数与状态（有截断即 ❌）③ 派发形态有 observed 级证据 ④ opcode/handler 清单完整且分级 ⑤ 加密参数落地字段 + initiator 栈 ⑥ 字段值与采集点关联 ⑦ redirect 链字节完整 ⑧ 原生事件零丢失 ⑨ Worker/iframe/wasm 用了的都覆盖（无证据但活性自检通过记 unknown 而非 fail）⑩ JS 轨迹与原生事件时序对齐。

**评分低先区分「能力缺口」与「本轮目标没跑这段代码」**——被动会话负载深度天然波动，探针的活性自检就是为这个区分服务的。

### 五、插桩还是被动：先跑 divergence 裁决

对未知目标，用分叉检测工具自动跑被动/插桩双会话并裁决：

```bash
/usr/local/bin/python3.12 scripts/reverse-browser-instrumentation-divergence.py \
  --target-url https://example.com/ --project-dir /absolute/path/to/project
```

裁决口径（schema 2：path token 段 + 子域 token + query 键名多重集归一化，加密参数扫 POST body）：**aligned** 插桩透明可直接深采；**minor** 噪音级差异；**diverged** 目标侦测到插桩——改走被动 profile；**gap** 产物不全先补采集。

实测形态参考：Akamai/PerimeterX/Incapsula/hCaptcha/抖音/reCAPTCHA 插桩透明；瑞数/Kasada/gsxt 加速乐改写敏感（插桩 diverged，被动链路证据完整）；Cloudflare 挑战页插桩侧不建议。

### 六、已知边界（撞上时不是 bug）

* 页面世界零容忍目标：任何 JS 层函数替换都会被语法级侦测，只能被动 + 引擎层证据；

* ServiceWorker realm 引擎不可达，如实记 `serviceworker-gap`；

* VM 内纯算术中间态（码元加减异或）不可见，清单第 ⑥ 条记 unknown；

* SSE/streaming 响应体需请求完成才可得；SW 合成/redirect/evicted 响应记 None 并计数；

* 多线并行实测时用 `scripts/mcp-cleanup.sh`（scoped 清理），不要用广谱 pkill 误杀别线服务器。

## 安全边界

* 所有原始证据默认包含敏感数据，工程目录权限默认限制为当前用户；

* 不上传、不遥测、不自动提交 Git；

* 不在 stdout/stderr 打印密码、Cookie、token 或代理认证信息；

* 不使用 browser profile 复用未显式指定的旧 session；

* 不自动填写第三方账号，不绕过验证码、登录挑战或风控；

* 仅在获得目标授权时分析目标页面和网络流量。

## 测试

Python 核心 browser-free 测试：

```bash
cd pythonlib
python3 -m pytest -q
```

MCP 适配器测试使用项目内置 server，并把浏览器项目的 `pythonlib/` 放入 `PYTHONPATH`：

```bash
cd integrations/camoufox-reverse-mcp
PYTHONPATH=../../pythonlib:src python3 -m pytest -q
```

原生 tracer 测试可以绕过未安装的图像测试依赖运行：

```bash
python3 -m pytest --noconftest \
  tests/test_property_tracer_runtime.py \
  tests/test_inject_trace_to_source.py -q
```

构建或打包前校验上游版本、reverse selector 和能力标记：

```bash
python3 scripts/validate_reverse_build.py
```

真实浏览器验收应只使用全新的工程目录和本地 `about:blank` 或授权测试页面，检查 manifest、raw 文件、trace、index 和异常退出状态；它不等同于 Google 登录端到端验收。

## 项目文档

* 能力模型（能力定义层 + 14 形态实测矩阵 + 验收清单）：`docs/vm-reverse-capability-model-2026-09-25.md`

* 设计说明：`docs/superpowers/specs/2026-09-23-reverse-analysis-browser-design.md`

* 基础平台计划：`docs/superpowers/plans/2026-09-23-reverse-analysis-browser-foundation.md`

* MCP JSON 契约：`docs/reverse-browser-mcp-contract.md`

* 各阶段实战报告：`docs/reverse8-phase*.md`（第五\~十四阶段）

* 原生构建说明：`docs/releases/`

## 许可证

本项目沿用仓库中的 MIT 许可证，详见 [`LICENSE`](LICENSE)。
