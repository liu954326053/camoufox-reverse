# Camoufox Reverse

Camoufox Reverse 是一个面向 Web 协议、混淆脚本、动态代码和 JavaScript 虚拟机分析的浏览器基础平台。它在 Camoufox 的 Gecko 层提供可选的 PropertyTracer，并把网络、脚本、Cookie、Storage、环境访问和执行事件保存到指定工程目录，供后续分析器建立调用链和值生成链。

本项目只提供逆向分析基础设施，不把任何具体站点逻辑写死在浏览器核心中。Google 登录、验证码、BotGuard 或其它第三方认证流程不会由本项目自动填写账号、绕过防护或收集凭据；真实目标分析必须在获得授权的前提下由使用者主动操作。

## 当前状态

已实现并验证的基础能力：

- 必填的 `project_dir` 和工程目录权限校验；
- 每次启动创建独立 `session_id`，显式 `resume_session` 才能恢复未完成 session；
- 原始网络、脚本、状态、截图和 trace 追加写入 session 的 `raw/` 或 `trace/`；
- raw 文件索引重建、事件丢失计数和异常退出后的 `incomplete` 状态；
- Python 启动 API、`reverse-browser` CLI 和独立 MCP 适配器共用同一套工程/session 核心；
- MCP/CLI 的路径 containment、关闭幂等、错误 envelope 和敏感值不回显；
- 随项目提供的中文 Agent Skill 与 Python MCP stdio client。

深层 SpiderMonkey 全量 opcode、VM 寄存器适配器、浏览器与 Go 实现的首个分歧定位仍属于后续计划。通过基础测试不等于某个第三方站点登录成功。

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

需要原生 trace 时，安装与当前 Python 包匹配的 reverse 构建。当前 selector 是：

```text
whitenightshadow/152.0.4-beta.30-reverse.5
```

请从对应 release 下载与你的平台匹配的归档，并使用 release 附带的安装器校验 SHA-256 后安装。不要把 reverse 构建解压到普通缓存目录，也不要覆盖已有 active 版本。安装完成后可检查：

```bash
python3 -m camoufox list
python3 -m camoufox active
```

普通 Camoufox 和 reverse 构建可以并存；只有显式传入 `--browser-version` 时才会选择 reverse 构建。

## CLI 使用

启动时 `--project-dir` 必填。按本机约定使用 `127.0.0.1:7890` 代理时：

```bash
reverse-browser launch \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --browser-version whitenightshadow/152.0.4-beta.30-reverse.5 \
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

MCP 服务器位于独立仓库 `camoufox-reverse-mcp`，它是本项目 Python 核心的薄适配层，不复制工程/session 实现。安装该服务器后，使用绝对工程目录启动：

```bash
cd /path/to/camoufox-reverse-mcp
python3 -m pip install -e .
camoufox-reverse-mcp \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --headless
```

MCP 的 `launch_browser` 要求 `project_dir`，默认捕获模式是 `raw`。核心工具包括浏览器启停、页面导航、网络请求、脚本保存、状态导出、PropertyTracer、动态脚本 instrumentation 和 session 文件查询。所有返回的 artifact 路径都必须位于当前 session 目录内。

MCP 契约见 [`docs/reverse-browser-mcp-contract.md`](docs/reverse-browser-mcp-contract.md)。独立 MCP client 位于 `integrations/camoufox-reverse-mcp-client/`，只使用 Python 标准库，通过 MCP stdio JSON Lines 协议连接服务器，不依赖 Node、浏览器或 JavaScript：

```bash
cd integrations/camoufox-reverse-mcp-client
python3 -m pip install -e .
python3 -m camoufox_reverse_mcp_client \
  --command camoufox-reverse-mcp \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  list-tools
```

## Agent Skill

中文 Skill 位于 `integrations/camoufox-reverse-skill/SKILL.md`。它规定了工程目录、session 隔离、原始证据、MCP/CLI 调用和安全边界，可复制到 `$CODEX_HOME/skills/camoufox-reverse-browser/` 或其它兼容 Agent 的 skill 目录。安装脚本默认只复制到指定 skill 目录，不修改宿主全局 MCP 配置：

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

### 发给 Agent 的自动安装消息

下面这段消息可以直接发送给支持本地命令执行的 Agent。它要求 Agent 只在当前仓库安装本项目附带的 Skill 和 MCP client，并先检查路径：

```text
请在当前 Camoufox Reverse 仓库中安装本项目附带的逆向分析能力：

1. 先确认当前目录包含 integrations/camoufox-reverse-skill、integrations/camoufox-reverse-mcp-client 和 scripts/install_reverse_browser_agent.py。
2. 使用 Python 3.10+ 执行：
   python3 scripts/install_reverse_browser_agent.py --project-dir /absolute/path/to/project --apply
3. 在 integrations/camoufox-reverse-mcp-client 中执行 python3 -m pip install -e .；不要安装 Node、浏览器自动化脚本或 JavaScript 运行时。
4. 按脚本输出把 camoufox-reverse MCP 服务器注册到当前 Agent 的 MCP 配置；配置必须使用绝对 project_dir，并使用 http://127.0.0.1:7890 作为代理地址。
5. 安装完成后只做 browser-free 校验：读取 Skill、执行 MCP client 的 list-tools 或 --help，并确认没有输出密码、Cookie、token；不要登录第三方网站、不要处理验证码、不要绕过认证或风控。
6. 报告实际修改的文件、安装路径和校验命令；不要把工程目录或 raw 证据加入 Git。
```

## 安全边界

- 所有原始证据默认包含敏感数据，工程目录权限默认限制为当前用户；
- 不上传、不遥测、不自动提交 Git；
- 不在 stdout/stderr 打印密码、Cookie、token 或代理认证信息；
- 不使用 browser profile 复用未显式指定的旧 session；
- 不自动填写第三方账号，不绕过验证码、登录挑战或风控；
- 仅在获得目标授权时分析目标页面和网络流量。

## 测试

Python 核心 browser-free 测试：

```bash
cd pythonlib
python3 -m pytest -q
```

MCP 适配器测试需要把核心源码放到 `PYTHONPATH`，并使用 MCP 仓库自己的虚拟环境：

```bash
PYTHONPATH=/path/to/camoufox-reverse/pythonlib:/path/to/camoufox-reverse-mcp/.worktrees/codex-project-scoped-reverse-sessions/src \
  /path/to/camoufox-reverse-mcp/.venv/bin/python -m pytest -q
```

原生 tracer 测试可以绕过未安装的图像测试依赖运行：

```bash
python3 -m pytest --noconftest \
  tests/test_property_tracer_runtime.py \
  tests/test_inject_trace_to_source.py -q
```

真实浏览器验收应只使用全新的工程目录和本地 `about:blank` 或授权测试页面，检查 manifest、raw 文件、trace、index 和异常退出状态；它不等同于 Google 登录端到端验收。

## 项目文档

- 设计说明：`docs/superpowers/specs/2026-09-23-reverse-analysis-browser-design.md`
- 基础平台计划：`docs/superpowers/plans/2026-09-23-reverse-analysis-browser-foundation.md`
- MCP JSON 契约：`docs/reverse-browser-mcp-contract.md`
- 原生构建说明：`docs/releases/`

## 许可证

本项目沿用仓库中的 MIT 许可证，详见 [`LICENSE`](LICENSE)。
