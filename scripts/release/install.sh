#!/usr/bin/env bash
# Camoufox Reverse reverse.9 零仓库自举安装脚本（自举包根目录）。
#
# 阶段：
#   1. 检查 python3 >= 3.10
#   2. pip 安装 pythonlib / integrations/camoufox-reverse-mcp / mcp（可重复执行）
#   3. 从 GitHub Release 下载并校验安装浏览器二进制（sha256 强制校验；
#      首次使用时先用官方 camoufox fetch 初始化 0.5 缓存，不改变 active 配置之外的任何状态）
#   4. 安装 Skill 并生成 MCP 配置示例（install_reverse_browser_agent.py --apply）
#   5. browser-free 自检：list-tools 能返回工具清单
#
# 环境变量（均可选）：
#   CAMOUFOX_REVERSE_PROJECT_DIR   证据工程目录（默认 ~/camoufox-reverse-evidence，
#                                  也可用第一个位置参数指定）
#   CAMOUFOX_REVERSE_RELEASE_BASE release 下载基地址（默认官方 GitHub Release；
#                                  测试可用 file:// 或本地 http 服务覆盖）
#   CAMOUFOX_REVERSE_CACHE_DIR     浏览器缓存目录（默认 camoufox 用户缓存，仅测试用）
#   CAMOUFOX_REVERSE_PROXY         写入 MCP 配置示例的浏览器代理
#                                  （默认 http://127.0.0.1:7890；GitHub 下载本身的
#                                  代理由 curl/python 尊重 http_proxy/https_proxy 环境变量，
#                                  本脚本不硬编码）
#   CAMOUFOX_REVERSE_SKIP_BROWSER  置 1 时跳过阶段 3（仅调试）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_BASE="${CAMOUFOX_REVERSE_RELEASE_BASE:-https://github.com/liu954326053/camoufox-reverse/releases/download/v152.0.4-beta.30-reverse.9}"
PROJECT_DIR="${1:-${CAMOUFOX_REVERSE_PROJECT_DIR:-$HOME/camoufox-reverse-evidence}}"
MCP_PROXY="${CAMOUFOX_REVERSE_PROXY:-http://127.0.0.1:7890}"
BROWSER_FOLDER="152.0.4-beta.30-reverse.9"
SELECTOR="whitenightshadow/$BROWSER_FOLDER"

fail() { echo "错误：$*" >&2; exit 1; }
step() { echo; echo "== [$1/5] $2"; }

# --- 阶段 1：Python 版本 ---
step 1 "检查 Python 环境"
command -v python3 >/dev/null 2>&1 || fail "找不到 python3，请先安装 Python 3.10 或更高版本"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "python3 版本过低（需要 >= 3.10）：$(python3 --version 2>&1)"
PY=python3
echo "Python 版本：$(python3 --version 2>&1)"

# --- 阶段 2：pip 安装三个包 ---
step 2 "安装 Python 组件（pythonlib / MCP server / MCP client）"
"$PY" -m pip install -e "$ROOT/pythonlib" -e "$ROOT/integrations/camoufox-reverse-mcp" -e "$ROOT/mcp" \
  || fail "pip 安装失败；如网络受限请先配置 pip 镜像或代理后重跑本脚本（幂等）"

# --- 阶段 3：浏览器二进制 ---
step 3 "下载并安装逆向浏览器二进制（$SELECTOR）"
if [ "${CAMOUFOX_REVERSE_SKIP_BROWSER:-0}" = "1" ]; then
  echo "按 CAMOUFOX_REVERSE_SKIP_BROWSER=1 跳过浏览器安装（仅调试用）"
else
  ASSET="$("$PY" "$ROOT/scripts/release_bootstrap.py" asset-name)" \
    || fail "无法确定当前平台对应的浏览器资产（支持 lin.x86_64 / mac.x86_64 / mac.arm64 / win.x86_64）"
  echo "平台资产：$ASSET"
  if [ -n "${CAMOUFOX_REVERSE_CACHE_DIR:-}" ]; then
    CACHE_DIR="$CAMOUFOX_REVERSE_CACHE_DIR"
  else
    CACHE_DIR="$("$PY" -c 'from camoufox.pkgman import INSTALL_DIR; print(INSTALL_DIR)')" \
      || fail "无法定位 camoufox 缓存目录"
  fi
  DEST="$CACHE_DIR/browsers/whitenightshadow/$BROWSER_FOLDER"
  if [ -d "$DEST" ]; then
    echo "浏览器已安装于 $DEST，跳过下载（幂等）"
  else
    if [ ! -f "$CACHE_DIR/.0.5_FLAG" ]; then
      echo "首次使用：先安装一个官方 Camoufox 以初始化 0.5 缓存（作为 active 保底浏览器）"
      "$PY" -m camoufox fetch \
        || fail "官方 Camoufox fetch 失败（需要访问 GitHub；可配置 http_proxy/https_proxy 后重跑）"
    fi
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/camoufox-reverse-install.XXXXXX")"
    trap 'rm -rf "$WORK"' EXIT
    SUMS_URL="$("$PY" "$ROOT/scripts/release_bootstrap.py" --base "$RELEASE_BASE" sums-url)"
    ASSET_URL="$("$PY" "$ROOT/scripts/release_bootstrap.py" --base "$RELEASE_BASE" asset-url "$ASSET")"
    echo "下载 SHA256SUMS：$SUMS_URL"
    curl -fsSL --retry 3 -o "$WORK/SHA256SUMS" "$SUMS_URL" \
      || fail "SHA256SUMS 下载失败：$SUMS_URL（如需代理请设置 https_proxy 环境变量）"
    EXPECTED="$("$PY" "$ROOT/scripts/release_bootstrap.py" expected-sha256 "$WORK/SHA256SUMS" "$ASSET")" \
      || fail "SHA256SUMS 中找不到 $ASSET，release 资产不完整，请报告发布方"
    echo "下载浏览器资产：$ASSET_URL"
    curl -fSL --retry 3 -o "$WORK/$ASSET" "$ASSET_URL" \
      || fail "浏览器资产下载失败：$ASSET_URL"
    INSTALL_ARGS=("$WORK/$ASSET" --sha256 "$EXPECTED")
    if [ -n "${CAMOUFOX_REVERSE_CACHE_DIR:-}" ]; then
      INSTALL_ARGS+=(--cache-dir "$CACHE_DIR")
    fi
    "$PY" "$ROOT/scripts/install-camoufox-reverse.py" "${INSTALL_ARGS[@]}" \
      || fail "浏览器安装校验未通过（sha256 或能力契约不匹配），未做任何改动"
  fi
fi

# --- 阶段 4：Skill + MCP 配置示例 ---
step 4 "安装 Agent Skill 并生成 MCP 配置示例"
mkdir -p "$PROJECT_DIR" || fail "无法创建工程目录：$PROJECT_DIR"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
"$PY" "$ROOT/scripts/install_reverse_browser_agent.py" \
  --project-dir "$PROJECT_DIR" \
  --proxy "$MCP_PROXY" \
  --mcp-config "$PROJECT_DIR/mcp-config.json" \
  --apply --force \
  || fail "Skill 安装或 MCP 配置示例生成失败"

# --- 阶段 5：browser-free 自检 ---
step 5 "browser-free 自检（list-tools，不启动浏览器）"
"$PY" -m camoufox_reverse_mcp_client \
  --command "$ROOT/scripts/run-reverse-mcp.sh" \
  --project-dir "$PROJECT_DIR" \
  list-tools \
  || fail "MCP 自检失败：list-tools 未返回工具清单"

echo
echo "安装完成："
echo "  - 浏览器 selector：$SELECTOR（不改变普通 Camoufox 的 active 配置）"
echo "  - Skill 已复制到 Agent skill 目录（默认 \$CODEX_HOME/skills/camoufox-reverse-browser）"
echo "  - MCP 配置示例：$PROJECT_DIR/mcp-config.json（请把其中 camoufox-reverse 条目追加进你的 MCP 配置，不要覆盖已有条目）"
echo "  - 证据工程目录：$PROJECT_DIR（含敏感数据，不要加入 Git，不要上传）"
