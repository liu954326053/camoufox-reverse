# reverse.6 构建与真实链路验收

日期：2026-09-25。平台：macOS arm64。浏览器代理：`http://127.0.0.1:7890`。

## 结论

固定版本已完成原生编译、打包、独立安装和启动验证。受控 HTTP fixture 的网络、脚本、状态及原生 trace 验收通过。真实 Dola 页面可以跳转至 Google 账号输入页，并保存对应请求与原生属性访问事件；真实站点的完整性验收未通过，两次 session 均为 `incomplete`。

本次没有提交账号密码，没有执行登录完成后的授权回调，没有获取 `id_token`，没有发送对话或视频任务。它不证明纯 Go Google 登录完成，也不证明完整 JS 值生成链已经实现。

## 构建产物

- Selector：`whitenightshadow/152.0.4-beta.30-reverse.6`。
- 构建脚本：[build-reverse-browser.sh](../scripts/build-reverse-browser.sh)。
- 归档：[camoufox-152.0.4-beta.30-mac.arm64.zip](../dist-reverse.6/camoufox-152.0.4-beta.30-mac.arm64.zip)，312878029 字节。
- SHA-256：`16ded0c73089b43fe9f32444322037773c935afbfd9345c987d89db10fe34456`。
- 安装目录：`/Users/magic/Library/Caches/camoufox/browsers/whitenightshadow/152.0.4-beta.30-reverse.6`；未修改 active 浏览器配置。
- Mach-O arm64 可执行文件启动返回 `Camoufox 152.0.4-beta.30`。

源码注入器 `--verify --strict --expect-hooks 77` 返回 `already=77`、`applied=0`、`files_changed=[]`。ZIP 的版本与 capability contract 校验通过；在 `dist-reverse.6` 目录执行 `shasum -a 256 -c camoufox-152.0.4-beta.30-mac.arm64.zip.sha256` 返回 OK。

打包后本轮修改了 shell 脚本、回归测试与文档，没有修改已打包的原生源码，因此未重复全量编译。

## 受控测试

执行 `scripts/reverse-browser-smoke.py --trace`，使用固定 selector 和本机代理。证据目录：

```text
/Users/magic/Workspace/mao-dispatch-sdk/dola/sources/web_google/artifacts/tasks/google-pure-protocol/reverse-browser-smoke-20260925/runs/d1cebdd98e4043958de08e91218f2ede
```

- manifest 为 `complete`，索引包含 35 个文件。
- 同一 URL 的两次不同 POST 请求体均保存；两份 262144 字节二进制响应逐字节校验通过。
- 外部脚本、Cookie、localStorage/sessionStorage 相关状态落盘通过 fixture 验收。
- 三个 trace 文件共 24 条事件；三个 metadata sidecar 均为 `off`，`dropped=0`。

## Dola → Google 实测

工程目录为 `artifacts/tasks/reverse-browser-design/live-smoke/dola-google-reverse6-20260925`。所有原始证据保留在各自 `runs/<session-id>` 中。

| 项目 | 登录入口测试 | Dola 预注入复测 |
| --- | --- | --- |
| Session | `e9a9d272e1034825ab8a4cdf2e695263` | `2b1da21a8ac64766a3ae0c42a5b305b1` |
| 页面范围 | Dola → Google identifier | Dola，导航前预注入 xhr/fetch |
| 网络 metadata | 483 | 454 |
| 保存的脚本文件 | 300 | 307 |
| 原生事件行数 | 2800 | 2100 |
| 原生 trace 文件 | 4 | 5 |
| 缺少 metadata sidecar | 1 | 2 |
| manifest | `incomplete` | `incomplete` |

第一轮实际点击 `Log In`、`Continue with Google`，落盘 document 链包含：

1. `www.dola.com/chat/`：200，响应体已保存。
2. `accounts.google.com/o/oauth2/v2/auth`：302，浏览器未提供重定向响应体。
3. `accounts.google.com/v3/signin/identifier`：200，响应体已保存；页面标题为 Google 登录，含 identifier 输入框。
4. `accounts.google.com/_/bscframe`：200，响应体已保存。

第一轮索引包含 1808 个文件。已有的三个 sidecar 显示 66、4、2730 条事件，均 `dropped=0`；第四个进程只有 `on` 状态且没有 sidecar，所以不能据此声称全 session 零丢失。索引的数值 `event_loss=0` 也不能覆盖这部分未知状态。

## 未通过项及证据边界

1. **MCP 请求发起栈未验收通过。** 第一轮 `get_request_initiator(12)` 返回 `source=unknown`、空栈。第二轮导航前注入 xhr/fetch 后，实际 `__mcp_xhr_shape_hooked_v3` 和 `__mcp_fetch_shape_hooked_v3` 为真，但旧 hook 标记为假，shape 日志条数均为零。本机 MCP 的 `hooks/xhr_hook.js`、`hooks/fetch_hook.js` 写入 shape 日志且未记录 stack；`tools/network.py:get_request_initiator` 读取旧日志名。该契约不一致已由源码确认；为什么实际请求未进入 shape 日志仍需单独定位执行上下文。浏览器包版本锁定不会自动修复 MCP 层的该问题。
2. **原生关闭确认不完整。** 第一轮出现 1 个 `native_stop_ack`、1 个 `native_sidecar` 和 `native_stop_quiescence`；第二轮分别为 2、2、1。第一轮缺失 sidecar 的 trace 为空，第二轮的两个文件已有 258 和 2 条事件，不能把它们当作无事件进程忽略。具体进程退出/停止时序原因尚未证明。
3. **部分网络证据不可获取。** 第一轮有 13 个 `response_body` 错误（包括重定向响应体不可用和协议层 request not found）及 1 个 `request_headers` 错误；第二轮有 2 个 `response_body` 错误。错误原文及关联 request ID 保留在各 session 的 `raw/capture-status.json`，未删除或降级成成功。
4. **PropertyTracer 的源位置不是 JS 调用栈。** 事件的 `s` 字段定位原生插桩点；它没有证明任意值的 JS 生成代码、VM 寄存器流或完整调用链已经还原。

后续修复应先统一 MCP hook 的原始日志与发起栈契约，并在导航前持久化证据；再定位真实多进程退出时的原生 flush/ack 缺口。未解决前保留 `incomplete` 状态，不能只靠放宽校验通过验收。

## 脚本回归

`python3 -m pytest --noconftest -q tests/test_build_reverse_browser.py`：9 passed。新增测试实际执行 shell，外部下载器与编译器用隔离替身替代，不启动真实编译。

覆盖不完整源码保留、损坏压缩包阻止解压、有效压缩包复用、跨工作目录调用、完整源码跳过重复 patch、伪 `_READY` 拦截、原生 hook 校验失败拦截，以及已有的计划/版本参数/预检契约。前四项先复现失败，再修复通过。

`bash -n scripts/build-reverse-browser.sh`、从 `/tmp` 调用 `--check-only`、`git diff --check` 均通过。源码包使用 `xz -t` 校验压缩流完整性，不将其表述为上游签名验证。
