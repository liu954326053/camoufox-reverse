# 集成组件

本目录包含浏览器项目的内置 MCP server。Skill 和 MCP client 位于项目根目录：

- `camoufox-reverse-mcp/`：MCP server 源码、hook、测试和文档。
- `../skill/`：给 Agent 使用的中文工作流和边界。
- `../mcp/`：只使用 Python 标准库的 MCP stdio client。

## 启动 MCP

从浏览器项目根目录执行，`project_dir` 必填，代理使用本机 `7890`：

```bash
bash scripts/run-reverse-mcp.sh \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --browser-version whitenightshadow/152.0.4-beta.30-reverse.8 \
  --headless
```

`run-reverse-mcp.sh` 会把项目内的 `pythonlib/` 和 MCP `src/` 放入
`PYTHONPATH`，不依赖 `/Users/magic/.codex/mcp-servers/`。

## 使用 Skill

Skill 文件位于：

```text
skill/SKILL.md
```

需要安装到 Agent 的全局 Skill 目录时，使用项目根目录的
`scripts/install_reverse_browser_agent.py`；它不会自动修改 MCP 配置。
