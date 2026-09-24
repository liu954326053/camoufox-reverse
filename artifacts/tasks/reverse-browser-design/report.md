# 逆向报告：reverse-browser-design

## 任务目标与结果

目标是在 Camoufox Python 启动层和独立 MCP 适配器中建立 project-scoped reverse browser foundation，并提供 CLI、Skill 和 Python MCP client。基础平台已通过 browser-free、原生 tracer、外部 MCP 和真实 headless 空白页验收；未宣称 Google 登录、验证码处理或 VM 端到端还原成功。

验证摘要：

- `python3 -m pytest -q`（`pythonlib/`）：`349 passed, 4 skipped`；
- `python3 -m pytest pythonlib/tests/test_reverse_mcp_contract.py pythonlib/tests/test_reverse_integration.py -q`：`24 passed`；
- 外部 MCP 根仓库测试：`92 passed`；
- `PYTHONPATH=integrations/camoufox-reverse-mcp-client python3 -m pytest integrations/camoufox-reverse-mcp-client/tests/test_client.py -q`：`8 passed`；
- `python3 -m pytest --noconftest tests/test_install_reverse_browser_agent.py -q`：`8 passed`；
- `python3 -m pytest --noconftest tests/test_property_tracer_runtime.py tests/test_inject_trace_to_source.py -q`：`14 passed`；
- 真实 CLI 与真实 MCP stdio 均用 `http://127.0.0.1:7890` 配置启动 reverse.5 headless 浏览器，访问空白页后以 `complete` 收尾，manifest 和 artifact 均在工程目录；
- `git diff --check` 通过，MCP client wheel 构建通过。

根目录原生测试若使用普通 `conftest.py` 收集会因本机未安装 `pixelmatch` 在收集阶段失败；绕过该图像依赖后的 14 个 tracer/injector 用例已通过。

## 实现路径

1. `ReverseProject` 和 `ReverseSession` 负责绝对工程目录、权限、session 隔离、manifest 状态和显式恢复。启动失败、异常关闭和事件丢失均保留 `incomplete` session。
2. `EvidenceStore` 以追加 JSONL/bytes 保存 raw 网络、脚本、状态和 trace 数据，用不可变 snapshot、哈希和确定性 index 支持异常后重建。
3. `reverse_launch_options` 复用现有 Camoufox `launch_options()`，把代理、reverse browser selector、私有 runtime 临时目录和 `propertyTrace.logDir` 注入当前 session。
4. `AsyncReverseBrowser` 监听请求/响应、保存原始字节、生成 storage 快照、停止 native trace，并在诊断失败时把错误写入 `raw/diagnostics/`。`about:blank` 的 opaque origin 被单独标为 `not_applicable`，不再误报为 capture failure。
5. `reverse_cli.py` 提供 `launch`、`session list`、`trace index` 和 `report build`。CLI 错误 envelope 使用静态安全消息，避免异常回显凭据。
6. 外部 `camoufox-reverse-mcp` 通过 `BrowserManager` 复用 core session/runtime；所有 navigation、network、script、storage、trace 和 environment artifact 都做 session containment。
7. `integrations/camoufox-reverse-mcp-client/` 用 Python 标准库实现 MCP JSON Lines stdio client，覆盖 initialize、通知、tools/list、tools/call、超时、未匹配 id 和 JSON-RPC 错误。
8. `integrations/camoufox-reverse-skill/SKILL.md` 和 `scripts/install_reverse_browser_agent.py` 提供中文 Skill、dry-run 安装、symlink containment、MCP 配置示例和无凭据代理归一化。

## 逆向思路

基础阶段优先把浏览器当作可回溯证据采集器，而不是把 Google 或某个 VM 的业务逻辑硬编码进核心。这样 CLI、MCP 和后续适配器共享同一 session、raw 和 index 边界，能够先验证数据完整性、路径隔离和生命周期，再在第二阶段增加 SpiderMonkey/VM hook。

真实浏览器验收选择新工程目录、`about:blank` 和 `127.0.0.1:7890` 配置，避免接触用户已有 profile、Google 账号或第三方站点。第一次 smoke 暴露了 about:blank sessionStorage 的预期安全限制，先写失败测试再将其改为 `not_applicable`；修复后 CLI 和 MCP smoke 都生成了 `complete` manifest。

PropertyTracer 原生 smoke 仍保守报告缺少 native dropped-counter/sidecar 的 trace 为 `incomplete`，不把无法证明的事件无丢失说成成功。这条边界保留给后续原生构建和深度执行计划。

## 难点与坑点

- 外部 MCP 测试若不设置 core 与 MCP source 的 `PYTHONPATH`，会导入 site-packages 中旧版 Camoufox，产生 `ImportError`；使用当前 core 和外部 source 的显式路径后 `92 passed`。
- 普通根测试收集依赖 `pixelmatch`，本机未安装时不能以普通 pytest 命令判断 tracer 失败；`--noconftest` 的专项结果才是本轮原生验证证据。
- trace-enabled reverse.5 当前 native status 文件没有完整 dropped counter，也没有 `.meta.json` sidecar。索引将 `event_loss_complete` 置为 false，并保留 diagnostics，避免虚假完整性结论。
- raw 证据故意不脱敏，live-smoke 工程目录可能包含完整请求/状态数据。它们没有加入 Git；使用者必须将工程目录视为敏感数据。
- MCP attach 模式不能拥有浏览器进程或保证 project-scoped raw capture，因此契约明确拒绝 `ws_endpoint`，要求由 MCP 自己启动 owned browser。

## 经验沉淀

- 任何浏览器启动入口都先校验 `project_dir`，并让 session 自己拥有 manifest、raw、trace 和 lock；不要在 MCP 层再造一套 cache 根目录。
- 任何返回给 CLI/MCP 的路径都先 `resolve()`，再检查是否位于当前 session；相对路径、`..` 和 symlink escape 都拒绝。
- `raw` 只追加，索引和报告从 raw 重建；注册 artifact 时先创建 snapshot，防止源文件在校验和登记之间被替换。
- stdio MCP 是一行一个 JSON-RPC 对象；client 只打印业务结果，server stderr 保存在内存诊断，不在超时/错误输出中回显原始命令、代理或 token。
- 真实空白页 smoke 适合验证启动、session、代理配置和关闭；真实网络/脚本/trace 验收应使用本地确定性 fixture 或已授权目标，不要把空白页结果当作协议成功。

## 交付物与复现

- `pythonlib/camoufox/reverse_project.py`：工程和 session 生命周期；
- `pythonlib/camoufox/reverse_evidence.py`：raw 写入、snapshot、index 和事件损失；
- `pythonlib/camoufox/reverse_launch.py`、`reverse_runtime.py`：启动配置和 owned runtime；
- `pythonlib/camoufox/reverse_cli.py`：JSON CLI；
- `docs/reverse-browser-mcp-contract.md`：MCP JSON 契约；
- `integrations/camoufox-reverse-mcp-client/`：无 Node/JavaScript 依赖的 Python MCP client；
- `integrations/camoufox-reverse-skill/SKILL.md`：中文 Agent Skill；
- `scripts/install_reverse_browser_agent.py`：dry-run/显式 apply 安装器；
- `README.md`：中文安装、CLI、MCP、Skill 和 Agent 自动安装消息；
- 外部仓库 `/Users/magic/Workspace/camoufox-reverse-mcp` 提交 `0d63835`：project-scoped MCP bridge。

复现基础测试：

```bash
cd /Users/magic/Workspace/camoufox-reverse/pythonlib
python3 -m pytest -q

cd /Users/magic/Workspace/camoufox-reverse
PYTHONPATH=integrations/camoufox-reverse-mcp-client \
  python3 -m pytest integrations/camoufox-reverse-mcp-client/tests/test_client.py -q
python3 -m pytest --noconftest tests/test_install_reverse_browser_agent.py -q
python3 -m pytest --noconftest tests/test_property_tracer_runtime.py tests/test_inject_trace_to_source.py -q
```

复现 CLI smoke 时使用新的绝对工程目录，并显式传 `--proxy http://127.0.0.1:7890`、`--browser-version whitenightshadow/152.0.4-beta.30-reverse.5`；不要复用本报告中的 session 或任何用户登录状态。
