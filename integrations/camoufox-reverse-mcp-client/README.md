# Camoufox Reverse MCP Client

这是一个只依赖 Python 标准库的 MCP stdio 客户端。它通过一行一个 JSON-RPC 消息连接 `camoufox-reverse-mcp`，不启动浏览器、不加载 JavaScript，也不会把代理密码或 token 写入输出。

```bash
python3 -m pip install -e .
python3 -m camoufox_reverse_mcp_client \
  --command camoufox-reverse-mcp \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  list-tools
```

调用工具：

```bash
python3 -m camoufox_reverse_mcp_client \
  --command camoufox-reverse-mcp \
  --project-dir /absolute/path/to/project \
  call check_environment --arguments '{}'
```

客户端只负责 MCP 传输和请求关联；工程目录校验、session 生命周期和原始证据落盘由服务器与 Python 核心负责。
