# Phase 14：MCP stdio 间歇断管根因裁决与修复（2026-09-26）

## 结论（一句话）

断管不是服务器启动竞态、锁竞争或超时，而是**并发工作线的"每轮实测前后
`pkill -f 'Python.framework/Versions/3.12.*camoufox_reverse_mcp'`"清理纪律**
把另一条线刚 spawn 的 MCP 服务器 SIGTERM 掉，客户端写 stdin 撞 EPIPE
（"MCP request could not be written"）或读 stdout 撞 EOF
（"MCP server closed stdout"）；重试时若无 pkill 落窗即成功——与 phase11/
phase13 观察到的"约半数首轮失败、重试可恢复"完全吻合。

## 证据链

1. **错误串定位**："MCP request could not be written" 逐字出自本仓库
   `mcp/camoufox_reverse_mcp_client/client.py` 的 `_write()`——仅在写 stdin
   抛 BrokenPipeError 时触发，即**服务器进程在客户端写入时已退出**。
2. **干净冷启动不复现**：25 轮独立冷启动（initialize + tools/list，逐轮新
   进程）0 失败，startup p50 = 0.41 s（min 0.395 / max 0.574），远低于客户
   端 30 s 超时——排除"import 链慢导致超时"与"stdout 未就绪"竞态。
   数据：`artifacts/analysis/phase14-stdio/coldstart_results.jsonl`。
3. **活捉并发 pkill**：排查期间 `ps` 当场抓到另一工作线的命令链
   `pkill -f 'Python.framework/Versions/3.12.*camoufox_reverse_mcp'; sleep 1;
   ... reverse-browser-instrumentation-divergence.py ...`（bash PID 55763），
   该模式匹配**所有** framework-Python 3.12 的 `camoufox_reverse_mcp` 进程，
   包括其他线刚启动的服务器。phase12/phase13 文档均记录了同款 pkill 纪律
   （如 `docs/reverse8-phase13-p2-probes-2026-09-26.md` 的两次断管注记）。
4. **错误形态复现**：受控实验（`probe_kill_shape.py`）——对握手期/会话期
   的服务器发 SIGTERM/SIGKILL，客户端逐字得到 "MCP request could not be
   written"；杀死落在写后读前则得到 "MCP server closed stdout"。与
   `artifacts/analysis/phase13-repro/repro_302_chain_retry.log` 中两种错误
   交替出现的形态一致。
5. **EOF 行为排除僵尸归因**：服务器 stdin EOF 后 10 s 内正常退出（rc=0），
   FastMCP 无 EOF 悬挂 bug；现场 3 个 codex 侧"僵尸"实为 stdin 仍被父进程
   持有的空闲服务器，不持有端口/锁，不会导致新进程启动失败。

### 变量二分记录

| 变量 | 结论 | 依据 |
| --- | --- | --- |
| 启动时序（stdout 未就绪即写） | 排除 | 管道写端在子进程 exec 后即开，25/25 成功 |
| 启动耗时/超时 | 排除 | p50 0.41 s ≪ 30 s 超时 |
| 端口/singleton/profile 锁 | 排除 | stdio 无端口；浏览器 profile 启动时才创建；无锁文件代码 |
| 僵尸进程串扰 | 排除 | EOF 探针：服务器正常退出；空闲进程不占共享资源 |
| playwright 驱动补丁竞争 | 排除（本轮条件） | 两处补丁均已幂等落盘，启动时只读不写 |
| **外部 SIGTERM（并发 pkill）** | **确认** | 活捉命令链 + 注入实验错误形态逐字一致 |

## 失败率统计

| 条件 | 轮数 | 失败 | 失败率 |
| --- | --- | --- | --- |
| phase13 实测（并发线活跃，修复前） | 约 6 次首轮尝试 | 约半数 | ~50%（日志实录 5 次失败/3 个会话） |
| 干净冷启动（无并发 pkill，修复前） | 25 | 0 | 0% |
| 每轮注入 SIGTERM + `start_retries=0`（对照） | 6 | 6 | 100% |
| 每轮注入 SIGTERM + `start_retries=2`（修复后） | 20 | 0 | **0%**（20/20 透明重生，t≈0.57 s/轮） |
| 干净冷启动（修复后复测） | 25 | 0 | 0% |

数据：`artifacts/analysis/phase14-stdio/{coldstart_results,killrace_results}.jsonl`；
脚本：同目录 `repro_coldstart.py`、`probe_kill_shape.py`、`verify_killrace.py`。

## 修复内容

1. **客户端握手自动重生**（`mcp/camoufox_reverse_mcp_client/client.py`）：
   新增 `start_retries`（默认 2）。`initialize()` 握手期内若服务器进程死亡
   （EPIPE / stdout EOF / 管道不全——逐字匹配本类自抛的四个错误串），自动
   清理尸体、重置 stdout 队列/请求 id/读线程状态并重生新进程重放握手。
   **握手完成后绝不重试**：会话态工具调用（launch_browser 等）有副作用，
   在新进程上重放语义错误，维持原样抛错。`_respawn()` 重建
   `_stdout_queue`，避免旧读线程的 EOF 哨兵毒化新进程读取。
2. **CLI 透传**（`mcp/camoufox_reverse_mcp_client/__main__.py`）：
   新增 `--start-retries N`（默认 2，0 关闭），`run-client.sh` 用户零改动受益。
3. **scoped 清理脚本**（`scripts/mcp-cleanup.sh`）：替代广谱 pkill 纪律。
   必须显式给选择器才执行：`--project-dir PATH`（精确匹配参数对，只杀自己
   的会话）、`--older-than MINUTES`（只杀残留老进程，保护别线新进程）、
   `--all`（旧行为，stderr 警告）、`--dry-run` 预演。macOS `ps` 无 `etimes`，
   内置 `etime`→秒解析器。
4. **测试**：`mcp/tests/test_client.py` 新增 5 条（EPIPE 重生、EOF 重生、
   `start_retries=0` 保持原失败契约、重试耗尽抛最后错误、会话期死亡不重试）。

## 回归

- 客户端套件：13/13 通过（8 旧 + 5 新）。
- 集成套件（`integrations/camoufox-reverse-mcp`，PYTHONPATH=src，
  /usr/local/bin/python3.12）：**167 passed, 2 skipped**——零回归。
- 未触碰 `vm_loop.py` / `network.py` / `browser.py` 及任何插桩语义。

## 残余风险

1. **会话期被杀仍不可恢复（设计如此）**：pkill 落在 launch_browser 之后，
   会话状态随进程死亡丢失，客户端照旧抛错，由调用方整会话重试
   （phase13 脚本已有此纪律）。根治靠各工作线改用 `scripts/mcp-cleanup.sh`
   的 scoped 选择器。
2. **修复只覆盖使用本仓库客户端的调用方**：codex 等第三方 MCP 宿主有自己的
   stdio 客户端，`start_retries` 管不到它们；它们受益于（3）的纪律切换。
3. **macOS jetsam/内存压力**理论上也能造成相同错误形态，本轮无证据支持，
   未排除为次要贡献因素；若改用 scoped 清理后仍偶发断管，应复查
   `log show --predicate 'eventMessage CONTAINS "jetsam"'`。
4. 启动期 playwright 驱动补丁的 rglob 全量读（每次启动读一遍 driver lib）
   是当前 0.4 s 启动成本的主要部分；非 bug，但若未来 driver 更新触发真正
   重写，并发双进程非原子读写同一 JS 文件的窗口仍在（极低概率），可考虑
   后续加写锁或临时文件+rename。
