# 第十五阶段：reverse.9 Release 直装管线（零仓库自举）——落地记录（2026-09-26）

日期：2026-09-26 ｜ 状态：**本地全部验证通过；GHA 未真跑**（遗留风险见 §6）

目标：别的机器上的 Agent 不需要 clone 仓库，只从 GitHub Release 下载产物即可完成安装
（浏览器二进制 + Skill + MCP）。

## 1. 改动文件清单

| 文件 | 改动 |
|---|---|
| `.github/workflows/build.yml` | 触发 tag → `v152.0.4-beta.30-reverse.9`（保留 workflow_dispatch）；**顺带修复一个存量 bug：`push` 原先嵌套在 `workflow_dispatch:` 之下，tag 推送实际永不触发 release**，已改为同级；release notes 校验与 `body_path` → `v152.0.4-beta.30-reverse.9.md`；release job 新增 `Package agent bootstrap bundle` 步骤（staging 目录 + cp 打包，GNU/BSD tar 行为一致），bundle sha256 追加进 SHA256SUMS，bundle 加入 action-gh-release files 与 upload-artifact |
| `docs/releases/v152.0.4-beta.30-reverse.9.md` | 新建，按 `_template.md`：版本说明 / 主要变更（3 条：引擎层 initiator 栈、document 302 直通 + CSP 护栏、MCP 引擎体捕获）/ 兼容性 / 验证 / 完整变更链接 |
| `scripts/release_bootstrap.py` | 新建。install.sh 的可测内核：uname→资产名平台映射（不支持的平台中文报错）、release URL 拼接、SHA256SUMS 解析（缺失/冲突/格式非法均报错） |
| `scripts/release/install.sh` | 新建（打包时置于包根 `install.sh`）。5 阶段薄壳：Python≥3.10 检查 → pip install -e 三包 → 浏览器二进制下载+sha256 强制校验安装（首次先 `camoufox fetch` 初始化 0.5 缓存）→ Skill 安装 + mcp-config.json 示例 → list-tools browser-free 自检。`set -euo pipefail`，每步中文错误提示，幂等 |
| `scripts/tests/test_release_bootstrap.py` | 新建，21 个单测（平台映射 11、URL 4、SHA256SUMS 解析 6） |
| `README.md` | 「发给 Agent 的自动安装消息（零仓库自举版）」整段从 clone-repo 流程重写为 release 直装流程；设计要点段同步重写 |
| `skill/SKILL.md` | selector `reverse.8` → `reverse.9`（2 处） |
| `integrations/camoufox-reverse-mcp/pyproject.toml` | **端到端干跑抓出的真实 bug**：`mcp>=1.0.0` 无上界，PyPI 现有 mcp 2.2.0 已删除 `mcp.server.fastmcp`，全新机器装完 server 起不来。改为 `mcp>=1.0.0,<2` |

未改动：`scripts/install-camoufox-reverse.py`、`scripts/install_reverse_browser_agent.py`、
`pythonlib/camoufox/reverse_compat.py`、引擎/插桩代码、根目录 `tests/`。

## 2. 自举包内容清单（`camoufox-reverse-agent-reverse.9.tar.gz`）

本地模拟产物：`/tmp/camoufox-reverse-agent-reverse.9.tar.gz`，1,682,240 字节，180 个条目，
sha256 `2cc57016…17b6`（本地模拟值，GHA 真实产物会不同）。

```text
install.sh                          # 包根自举脚本（来自 scripts/release/install.sh）
pythonlib/                          # camoufox Python 包（含 reverse_compat.py 契约）
mcp/                                # camoufox_reverse_mcp_client（纯标准库 client）
integrations/camoufox-reverse-mcp/  # MCP server 源码 + 测试
skill/SKILL.md                      # 中文 Agent Skill
scripts/install-camoufox-reverse.py       # 浏览器 zip 校验安装器
scripts/install_reverse_browser_agent.py  # Skill/MCP 配置安装器
scripts/run-reverse-mcp.sh                # MCP server 启动壳
scripts/mcp-cleanup.sh                    # MCP 进程清理
scripts/release_bootstrap.py              # 平台映射/URL/哈希内核
```

已确认：包内目录布局与仓库相同（`__file__` 相对路径推导成立）；无 `__pycache__`、
`*.egg-info`、`.pytest_cache`；包内 pyproject 已是 `mcp>=1.0.0,<2`。

## 3. install.sh 各阶段验证结果（/tmp 解压副本 + venv 端到端真跑）

环境：`/usr/local/bin/python3.12` 建的隔离 venv；假 release 目录
`file:///tmp/fake-release`（含按 reverse.9 能力契约构造的 mac.arm64 zip + SHA256SUMS）；
`CAMOUFOX_REVERSE_CACHE_DIR` 指向预置 `.0.5_FLAG` 的假缓存。

| 阶段 | 结果 |
|---|---|
| 1. Python 版本检查 | 通过（Python 3.12） |
| 2. pip install -e 三包 | 通过：camoufox 0.5.6 / camoufox-reverse-mcp 1.1.0 / camoufox-reverse-mcp-client 1.0.0 全部 editable 构建成功，setup 元数据在包内路径下工作 |
| 3. 浏览器二进制 | 通过：平台映射出 `camoufox-152.0.4-beta.30-mac.arm64.zip`，curl 下载，sha256 校验一致，装到 `cache/browsers/whitenightshadow/152.0.4-beta.30-reverse.9`，selector 正确，`active_config_changed: false` |
| 4. Skill + MCP 配置 | 通过：skill 复制到 `$CODEX_HOME/skills/camoufox-reverse-browser`，`mcp-config.json` 生成于工程目录 |
| 5. list-tools 自检 | 通过（修复 mcp<2 后）：返回完整工具清单（launch_browser 等），未启动浏览器 |
| 幂等性 | 通过：第二次运行阶段 3 提示「浏览器已安装…跳过下载」，阶段 4 `--force` 覆写不炸 |
| 负向：sha256 不匹配 | 通过：`SHA256 mismatch` + 中文错误「浏览器安装校验未通过…未做任何改动」，exit 1，缓存目录无残留 |

## 4. 静态验证

- `build.yml` PyYAML 解析通过；`on` 触发器修复后为 `{workflow_dispatch, push(tags=[v152.0.4-beta.30-reverse.9])}`；release files/artifact 清单均含 bundle。
- release notes 用 workflow 同款校验脚本复制品验证通过（headings 合法、主要变更 3 条、含「**完整变更**：」）。
- `scripts/validate_reverse_build.py` 本地运行 `status: verified`，`reverse_release: reverse.9`——校验器与 BUILD-MANIFEST 生成全部从 `reverse_compat.py` 动态读取，无 reverse.8 写死（已 grep 确认）。

## 5. 回归数字（全部实跑）

| 套件 | 结果 |
|---|---|
| scripts/tests | **66 passed**（基线 45 + 新增 21） |
| pythonlib/tests | **363 passed, 5 skipped**（基线持平） |
| integrations/camoufox-reverse-mcp/tests | **167 passed, 2 skipped**（基线持平） |

## 6. 遗留风险

1. **GHA 未真跑**：build matrix、artifact 下载、bundle 打包步骤只在本地以同款命令模拟过；首次真跑可能暴露 runner 环境差异（已用 cp+staging 规避 GNU/BSD tar 差异）。
2. **首次使用路径未端到端覆盖**：干跑中 `.0.5_FLAG` 是手工预置的，`python3 -m camoufox fetch`（官方浏览器下载初始化缓存）未真跑；全新机器阶段 3 会多一次官方 Camoufox 下载。
3. **release 尚不存在**：`v152.0.4-beta.30-reverse.9` tag 未推送、资产未上传，当前按 README 消息执行会停在阶段 3 下载 404——属发布流程待办，脚本会如实报错。
4. macOS 上 `/tmp` 是 symlink，`install_reverse_browser_agent.py` 的 symlink 祖先防护会拒绝 `/tmp` 下的 CODEX_HOME/工程目录（干跑改用 `/private/tmp` 通过）；真实用户的 `~/.codex` 不受影响。
5. MCP 配置示例中的浏览器代理默认 `http://127.0.0.1:7890`（可用 `CAMOUFOX_REVERSE_PROXY` 覆盖）；GitHub 下载代理由 curl/pip 尊重 `http_proxy`/`https_proxy` 环境变量，脚本未硬编码。
6. bundle 未做签名/公证，完整性依赖 SHA256SUMS 与 release 同源发布（与浏览器 zip 现有信任模型一致）。
