# Google 登录 VM（BotGuard）实战能力检验

日期：2026-09-25。平台：macOS arm64。浏览器：`whitenightshadow/152.0.4-beta.30-reverse.8`。代理：`http://127.0.0.1:7890`。

实战链路：`dola.com/chat/` → `Log In` → `Continue with Google` → `accounts.google.com` identifier 页 → 提交随机不存在标识触发 BotGuard VM 与登录 RPC。不提交真实账号、不进入密码步骤、不处理验证码。

检验脚本：[reverse-browser-google-login-smoke.py](../scripts/reverse-browser-google-login-smoke.py)。证据工程目录：`artifacts/tasks/reverse-browser-design/live-smoke/dola-google-vm-20260925/`，通过 session：`runs/a12c51c98f29454680f2c1b9feb777bc`。

## 结论

设计文档第一阶段验收第 6 条要求的"从登录请求反查执行证据"在真实 BotGuard VM 场景下成立：

| 能力 | 结果 |
| --- | --- |
| 到达 identifier 页（GlifWebSignIn 全流程） | 通过，标题 `Sign in - Google Accounts` |
| BotGuard VM 加载观测 | `botguard.bg` 主世界对象存在（VM 内核内嵌于页面） |
| VM proof 值捕获 | MI613e 请求体 `f.req[37][2][0][1]` 含 2453 字符 `!` 前缀 proof，原始字节完整 |
| 登录 RPC 关联 | `batchexecute?rpcids=MI613e`（标识查找 RPC），栈关联 `exact` |
| 请求发起栈 | 采样 3/3 返回真实栈，穿透到 `AccountsSignInUi` gstatic bundle 的 `_.k.send`/`_.k.transfer` |
| 原生 PropertyTracer | 5 个进程 trace 全部带 sidecar，共 1936 事件，`dropped=0` |
| 索引与事件丢失 | 索引 3749 文件，`event_loss=0` |
| manifest | `incomplete`，唯一原因是 1 个 OAuth 302 重定向响应体协议不可用（见下文） |

## 实战中发现并修复的能力缺口

1. **`evaluate_js` 无法主世界执行（MCP 层缺陷，已修复）。** 该工具把表达式嵌进 `return ...;` 清理包装器，`mw:` 前缀被解析成标签，报 `unexpected token ':'`，主世界探针（hook 状态、BotGuard 对象）全部不可用。修复：`mw:` 表达式绕过包装器直达 `page.evaluate`，保持返回值清理语义。回归测试 4 个加入 `tests/test_evaluate_js_v101.py`，套件 92 passed。
2. **裸 GET identifier 页返回 400（链路认知修正，非浏览器缺陷）。** Google 要求经 ServiceLogin/OAuth 重定向携带 `flowName`/`dsh` 参数；curl 同样 400。实战脚本改为真实入口链路。
3. **B4hajb 在当前流程不出现（验收目标修正）。** 标识提交实际触发 `rpcids=MI613e`；`B4hajb` 属于更靠后的步骤。脚本改为识别 `batchexecute` 的 rpcids 集合并在请求体内定位 VM proof。

## 保留的已知边界（未降级、未放宽）

- 重定向响应体不可用仍记为 `response_body` 错误并标记 session `incomplete`。这是 `test_redirect_unknown_or_nonzero_body_is_not_invented` 固定的契约：不臆造未知字节、不把未知降级为成功。capabilities 中已声明。
- `xhr/fetch` hook 栈顶部两帧是 hook 自身（`debugger eval code`），分析器需跳过前两帧读业务栈。
- BotGuard VM 程序未通过 `/js/bg/` 网络请求加载——当前登录流程 VM 内核内嵌在页面脚本中，proof 生成在页面内完成。
- 原生 trace 的 `s` 字段是插桩点位置，不是完整 JS 调用栈；VM opcode/寄存器级还原仍属第二阶段。

## 环境注意

本机另有一份独立的 editable 安装副本 `/Users/magic/Workspace/camoufox-reverse-mcp`，落后于仓库内 `integrations/camoufox-reverse-mcp`（缺 `network_raw.js`、`network_evidence.py` 和本次 `mw:` 修复）。务必通过 `scripts/run-reverse-mcp.sh` 或显式 `PYTHONPATH` 使用仓库内代码，直接 `python -m camoufox_reverse_mcp` 会误用旧副本。

## 回归

- `integrations/camoufox-reverse-mcp`：`92 passed, 2 skipped`
- `pythonlib`：`356 passed, 5 skipped`
