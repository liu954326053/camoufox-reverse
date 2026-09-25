# Task 2 验收记录：解释器循环插桩与 VM 轨迹捕获（2026-09-25）

第二阶段 Task 2 的实战验收记录。目标：对 Dola→Google 登录链路中的 BotGuard VM
解释器循环做通用插桩，产出迭代计数与状态快照轨迹，落盘到 session raw/。

## 结论

通过。实战 session `e36c9c6186bf42a990db92b0874778dd`：

- 捕获 124 个循环、合计 330 456 次迭代；
- 其中 BotGuard VM 派发循环（bscframe iframe realm）单轮 148 068 次迭代，
  状态快照序列（如 33→14→4→70→75→92→8→11…）完整落盘；
- proof 正常生成（`f.req` 内 2 785 字符 `!` 前缀串，rpcid `MI613e`），
  插桩未破坏页面语义；
- tick 开销 348 ns/次（33 万次调用合计约 115 ms）。

轨迹文件：
`artifacts/tasks/reverse-browser-design/live-smoke/dola-google-vm-20260925/runs/e36c9c6186bf42a990db92b0874778dd/raw/vm-loop/5e29b6643e8c491e8cfa101e7e9a562b.json`

## 四轮定位过程（每轮都有实证依据）

1. **HTML 静态改写无效**：identifier 页 49 处 `while(` 全部是 `\u003d` 转义的
   函数字符串（控制流平坦化），esprima 看到的是字符串不是代码。
2. **eval/Function hook 静默**：CSP `require-trusted-types-for 'script'`，
   BotGuard 用 Trusted Types policy 的 `createScript` 物化动态脚本。
   补 TT 通道后首次捕获（2 个循环、36 次迭代）。
3. **只抓到加载器**：加载器（66 KB，13 个平坦化循环）会把 VM 放进
   `https://accounts.google.com/_/bscframe` iframe 的独立 realm 执行
   （`document.createElement("iframe")` → 取 `contentWindow` → 用其
   trustedTypes/eval）。顶层 hook 够不到 iframe realm。
4. **跨 realm 聚合解决**：运行时改为可自我复制的安装器，iframe 挂载/加载时
   装入 contentWindow；注入的 tick 调用带 realm 自适应 fallback
   （本 realm → window.top → 纯条件求值，无运行时也不改语义）；
   drain 聚合顶层与所有同源 frame。本轮即捕获 148 068 次迭代的派发循环。

## 产物

- `hooks/vm_loop_trace.js`：tick 运行时 + 六类动态通道 hook
  （eval、Function 族含 `(fn).constructor` 绕过与 async/generator 变体、
  TT createPolicy 包装、script 元素/iframe 挂载、Worker 记录、
  setTimeout/setInterval 字符串）；while 与 for 循环都注入；
  `for(;;){switch(X){…}}` 解释器形态自动快照判别变量 X；
  所有动态源码（含 0 循环）记录 kind/长度/哈希/前缀，有循环的保留全文。
- `tools/vm_loop.py`：`vm_loop_trace(action=install/log/stop)`；
  drain 走 `drainAllText` 跨 realm 聚合，写 `raw/vm-loop/<uuid>.json`。
- `utils/loop_rewriter.py`：document HTML 静态改写（route 路径保留，
  覆盖非转义内联脚本）。
- 测试：python 17 项（`tests/test_vm_loop_trace.py`）+ node 离线注入验证
  22 项（`tests/verify_vm_loop_inject.node.js`，用 Kimi 自带 node 跑）；
  全量回归 109 + 6 + 356 绿。

## 已知边界

- iframe 为跨域或 sandbox 去 allow-same-origin 时，realm 安装会失败并记录
  `iframe-install-fail`，注入代码退化为纯条件求值（不改语义）；
- Worker realm 未覆盖（本轮 `worker-src 'self'` 但 BotGuard 未用 Worker，
  只有记录没有插桩）；
- 静态 route 改写（loop_rewriter.py）的循环 id 用 `L<offset>`，与动态路径的
  `L<hash>_<offset>` 不同源时可能撞 id，实测 Google 链路全走动态路径，暂未触发。
