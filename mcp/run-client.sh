#!/usr/bin/env bash
# Use the MCP client bundled in this browser project to start its MCP server.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BROWSER_ROOT="${CAMOUFOX_REVERSE_ROOT:-$ROOT}"
PROJECT_DIR=""
PROXY="http://127.0.0.1:7890"

usage() {
    cat <<'EOF'
Usage: mcp/run-client.sh --project-dir PATH [options] list-tools
       mcp/run-client.sh --project-dir PATH [options] call TOOL --arguments JSON

Options:
  --browser-project-dir PATH  Browser project containing scripts/run-reverse-mcp.sh
  --project-dir PATH          Required evidence project directory
  --proxy URL                 Proxy URL (default: http://127.0.0.1:7890)
EOF
}

client_args=()
while (($#)); do
    case "$1" in
        --browser-project-dir)
            (($# >= 2)) || { usage >&2; exit 2; }
            BROWSER_ROOT="$2"
            shift 2
            ;;
        --project-dir)
            (($# >= 2)) || { usage >&2; exit 2; }
            PROJECT_DIR="$2"
            shift 2
            ;;
        --proxy)
            (($# >= 2)) || { usage >&2; exit 2; }
            PROXY="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            client_args+=("$@")
            break
            ;;
    esac
done

[[ -n "$PROJECT_DIR" ]] || { echo "mcp client: --project-dir is required" >&2; exit 2; }
SERVER="$BROWSER_ROOT/scripts/run-reverse-mcp.sh"
[[ -x "$SERVER" ]] || { echo "mcp client: missing executable server: $SERVER" >&2; exit 2; }

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m camoufox_reverse_mcp_client \
    --command "$SERVER" \
    --project-dir "$PROJECT_DIR" \
    --proxy "$PROXY" \
    "${client_args[@]}"
