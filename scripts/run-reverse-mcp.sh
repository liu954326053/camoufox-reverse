#!/usr/bin/env bash
# Run the vendored reverse MCP server with this browser repository's Python core.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MCP_SRC="$ROOT/integrations/camoufox-reverse-mcp/src"
CORE_SRC="$ROOT/pythonlib"

export PYTHONPATH="$CORE_SRC:$MCP_SRC${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m camoufox_reverse_mcp "$@"
