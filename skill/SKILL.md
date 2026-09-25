---
name: camoufox-reverse-browser
description: 使用 Camoufox Reverse 的 CLI 和 MCP 进行授权 Web 逆向取证、原始证据管理和执行链分析。
---

# Camoufox Reverse 浏览器

## 适用范围

这个 Skill 用于授权的 Web 协议、混淆脚本、动态代码、环境指纹和 JavaScript 虚拟机取证。浏览器是证据采集器，协议还原、算法复现和 Go/Python 实现属于后续分析工作；不要把浏览器能启动等同于目标站点端到端成功。

## 启动前检查

1. 必须使用一个绝对的 `project_dir`。每次启动默认创建新的隔离 session。
2. 先确认工程目录可写，并确认它不在 Git 提交范围内。raw 证据可能包含 Cookie、Storage、请求体和 token。
3. 需要代理时显式传 `http://127.0.0.1:7890`；不要从聊天记录、环境变量输出或异常信息中复制代理密码。
4. 原生 PropertyTracer 默认使用项目固定的 `whitenightshadow/152.0.4-beta.30-reverse.9`；不改变普通 Camoufox 的 active 版本。

## CLI 路线

```bash
reverse-browser launch \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --browser-version whitenightshadow/152.0.4-beta.30-reverse.9 \
  --trace-profile targeted
```

优先使用 `--duration` 或明确的终止信号关闭浏览器。结束后检查 `runs/<session-id>/manifest.json`、`raw/`、`trace/` 和 `indexes/`；遇到异常时保留 `incomplete` session，不覆盖旧 raw 文件。

## MCP 路线

本 Skill 配套的 MCP client 位于浏览器项目根目录的 `mcp/`，MCP server 源码位于 `integrations/camoufox-reverse-mcp/`，可以直接通过项目根目录的 `mcp/run-client.sh` 调用，不需要宿主机隐藏的 MCP checkout。

MCP 服务器启动参数至少包含：

```text
--project-dir /absolute/path/to/project
--proxy http://127.0.0.1:7890
```

调用 `launch_browser` 时 `project_dir` 必填，`capture_profile` 使用 `raw`。使用 `network_capture`、`scripts`、`trace_property_access`、`check_environment` 和 `export_state` 时，只接受当前 session 内的 artifact 路径。关闭使用 `close_browser`，重复关闭应返回已关闭状态。

MCP client 只负责 stdio JSON Lines 传输。直接使用项目内置 client：

```bash
bash mcp/run-client.sh \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  list-tools
```

调用具体工具：

```bash
bash mcp/run-client.sh \
  --project-dir /absolute/path/to/project \
  call check_environment --arguments '{}'
```

## 取证纪律

- 先列出 2 到 5 个高信息量入口，再启用定向 trace；不要无目标地记录整个 JS 引擎。
- 将请求 ID、脚本 hash、源码位置、调用帧和 trace 序号关联起来；仅凭时间相近不能断言数据依赖。
- `observed` 只用于直接捕获的输入/输出，`call-linked` 只用于调用关系，`inferred` 必须注明推断，缺失链路标为 `gap`。
- raw 只追加；分析结果写到 `derived/`、`report/` 或 `indexes/`，不能覆盖 raw。
- 所有 stdout/stderr 不得出现密码、Cookie、token 或完整代理认证信息。

## 收尾验收

至少确认：启动缺少 `project_dir` 会失败；新启动生成唯一 session；raw/trace 均在工程目录；关闭后 manifest 是 `complete` 或明确的 `incomplete`；索引可以从 raw 重建；没有文件写到工程目录之外。真实目标页面只在得到授权后访问。
