#!/usr/bin/env bash
# Scoped cleanup for camoufox_reverse_mcp server processes.
#
# Replaces the blanket `pkill -f '...camoufox_reverse_mcp'` discipline, which
# kills servers belonging to *other* concurrent work lines (reverse8 phase14
# root cause of the intermittent "MCP request could not be written" stdio
# failures: a fresh server SIGTERM-ed mid-handshake by a sibling line's
# cleanup).
#
# A selector is REQUIRED; this script never defaults to killing everything.
#
# Usage:
#   scripts/mcp-cleanup.sh --project-dir PATH [--older-than MINUTES] [--dry-run]
#   scripts/mcp-cleanup.sh --older-than MINUTES [--dry-run]
#   scripts/mcp-cleanup.sh --all [--dry-run]   # old blanket behavior, warns
#
# Exit code: 0 (also when nothing matched), 2 on usage error.

set -euo pipefail

PROJECT_DIR=""
OLDER_THAN=""
DRY_RUN=0
ALL=0

usage() { sed -n '2,17p' "$0"; }

while (($#)); do
    case "$1" in
        --project-dir) (($# >= 2)) || { usage >&2; exit 2; }; PROJECT_DIR="$2"; shift 2 ;;
        --older-than)  (($# >= 2)) || { usage >&2; exit 2; }; OLDER_THAN="$2"; shift 2 ;;
        --dry-run|-n)  DRY_RUN=1; shift ;;
        --all)         ALL=1; shift ;;
        -h|--help)     usage; exit 0 ;;
        *) echo "mcp-cleanup: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ -z "$PROJECT_DIR" && -z "$OLDER_THAN" && "$ALL" -eq 0 ]]; then
    echo "mcp-cleanup: refusing to kill without a selector; use --project-dir, --older-than, or --all" >&2
    exit 2
fi

min_etimes=0
if [[ -n "$OLDER_THAN" ]]; then
    [[ "$OLDER_THAN" =~ ^[0-9]+$ ]] || { echo "mcp-cleanup: --older-than must be integer minutes" >&2; exit 2; }
    min_etimes=$((OLDER_THAN * 60))
fi

if [[ "$ALL" -eq 1 ]]; then
    echo "mcp-cleanup: WARNING: --all kills servers of other concurrent work lines too" >&2
fi

killed=0
# Convert ps(1) etime ([[dd-]hh:]mm:ss) to seconds. macOS ps has no `etimes`.
etime_to_seconds() {
    local etime="$1" days=0 hms
    hms="$etime"
    if [[ "$hms" == *-* ]]; then
        days="${hms%%-*}"
        hms="${hms#*-}"
    fi
    local IFS=:
    # shellcheck disable=SC2206
    local parts=($hms)
    unset IFS
    local secs=0
    case "${#parts[@]}" in
        3) secs=$(( 10#${parts[0]} * 3600 + 10#${parts[1]} * 60 + 10#${parts[2]} )) ;;
        2) secs=$(( 10#${parts[0]} * 60 + 10#${parts[1]} )) ;;
        *) echo -1; return ;;
    esac
    echo $(( days * 86400 + secs ))
}

# shellcheck disable=SC2009
while read -r pid _; do
    [[ -n "$pid" ]] || continue
    etimes=$(etime_to_seconds "$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')")
    [[ "$etimes" != "-1" && -n "$etimes" ]] || continue
    command=$(ps -o command= -p "$pid" 2>/dev/null || true)
    [[ "$command" == *camoufox_reverse_mcp* ]] || continue
    if [[ -n "$PROJECT_DIR" ]]; then
        # exact argument match: "--project-dir PATH" as a token pair
        [[ "$command" == *"--project-dir $PROJECT_DIR"* ]] || continue
    fi
    if [[ -n "$OLDER_THAN" ]]; then
        (( etimes >= min_etimes )) || continue
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "would kill $pid (age=${etimes}s): $command"
    else
        echo "killing $pid (age=${etimes}s): ${command:0:120}"
        kill "$pid" 2>/dev/null || true
    fi
    killed=$((killed + 1))
done < <(pgrep -f 'camoufox_reverse_mcp' | grep -v "^$$\$" || true)

echo "mcp-cleanup: $killed process(es) matched"
