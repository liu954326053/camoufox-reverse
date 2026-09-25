#!/usr/bin/env bash
# Build and package the pinned macOS arm64 Camoufox Reverse browser.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$ROOT/upstream.sh"

TARGET="macos"
ARCH="arm64"
OUTPUT_DIR="$ROOT/dist"
MOZ_STATE="${MOZBUILD_STATE_PATH:-$ROOT/.mozbuild-reverse}"
MIN_FREE_GIB="30"
PLAN_ONLY=false
CHECK_ONLY=false
SKIP_DEPENDENCY_CHECK=false
ALLOW_LOW_DISK=false
INSTALL=false
KEEP_SOURCE=true

usage() {
    cat >&2 <<'EOF'
Usage: scripts/build-reverse-browser.sh [options]

Build options:
  --plan                    Print the pinned build plan as JSON.
  --check-only              Run contract and machine preflight checks.
  --skip-dependency-check   Skip executable dependency checks.
  --allow-low-disk          Allow a build with less than the recommended free space.
  --install                 Install the verified archive side-by-side after packaging.
  --output-dir PATH         Put the archive and checksum under PATH.
  --mozbuild-state PATH     Isolate Mozilla build state under PATH.
  --min-free-gib N          Override the recommended free-space threshold.
  --no-keep-source          Remove the generated source tree after a successful build.
  --target TARGET           Must be macos for this pinned script.
  --arch ARCH               Must be arm64 for this pinned script.
EOF
}

fail() {
    echo "build-reverse-browser: $*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --plan) PLAN_ONLY=true ;;
        --check-only) CHECK_ONLY=true ;;
        --skip-dependency-check) SKIP_DEPENDENCY_CHECK=true ;;
        --allow-low-disk) ALLOW_LOW_DISK=true ;;
        --install) INSTALL=true ;;
        --no-keep-source) KEEP_SOURCE=false ;;
        --output-dir)
            (($# >= 2)) || fail "--output-dir requires a path"
            OUTPUT_DIR="$2"
            shift
            ;;
        --mozbuild-state)
            (($# >= 2)) || fail "--mozbuild-state requires a path"
            MOZ_STATE="$2"
            shift
            ;;
        --min-free-gib)
            (($# >= 2)) || fail "--min-free-gib requires a number"
            MIN_FREE_GIB="$2"
            shift
            ;;
        --target)
            (($# >= 2)) || fail "--target requires a value"
            TARGET="$2"
            shift
            ;;
        --arch)
            (($# >= 2)) || fail "--arch requires a value"
            ARCH="$2"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) fail "unknown option: $1" ;;
    esac
    shift
done

[[ "$TARGET" == "macos" && "$ARCH" == "arm64" ]] || \
    fail "this pinned build script supports macOS arm64 only"
[[ "$MIN_FREE_GIB" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
    fail "--min-free-gib must be a non-negative number"

# Resolve caller-supplied paths before running repository-relative make targets.
OUTPUT_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$OUTPUT_DIR")"
MOZ_STATE="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$MOZ_STATE")"
cd "$ROOT"

SOURCE_DIR="$ROOT/camoufox-${version}-${release}"
TARBALL="$ROOT/firefox-${version}.source.tar.xz"
SELECTOR="whitenightshadow/${version}-${release}-${reverse_release}"
ARCHIVE_NAME="camoufox-${version}-${release}-mac.${ARCH}.zip"
ARCHIVE="$OUTPUT_DIR/$ARCHIVE_NAME"
CHECKSUM="$ARCHIVE.sha256"

free_bytes() {
    df -Pk "$ROOT" | awk 'NR == 2 {print $4 * 1024}'
}

free_gib() {
    python3 - "$(free_bytes)" <<'PY'
import sys
print(f"{int(sys.argv[1]) / (1024 ** 3):.2f}")
PY
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

check_dependencies() {
    local command_name
    for command_name in make python3 clang clang++ tar xz 7z shasum; do
        require_command "$command_name"
    done
    if ! command -v aria2c >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
        fail "source download requires aria2c or curl"
    fi
    command -v rustup >/dev/null 2>&1 || command -v rustc >/dev/null 2>&1 || \
        fail "Rust toolchain is required"
}

contract_check() {
    python3 "$ROOT/scripts/validate_reverse_build.py" \
        --version "$version" \
        --release "$release" \
        --reverse-release "$reverse_release" >/dev/null
}

preflight_payload() {
    local current_free
    current_free="$(free_gib)"
    python3 - "$current_free" "$SOURCE_DIR" "$SELECTOR" "$TARGET" "$ARCH" "$MOZ_STATE" "$OUTPUT_DIR" "$MIN_FREE_GIB" <<'PY'
import json
import sys

free_gib, source_dir, selector, target, arch, moz_state, output_dir, min_free_gib = sys.argv[1:]
print(json.dumps({
    "free_gib": float(free_gib),
    "min_free_gib": float(min_free_gib),
    "source_dir": source_dir,
    "selector": selector,
    "target": target,
    "arch": arch,
    "mozbuild_state": moz_state,
    "output_dir": output_dir,
}, ensure_ascii=False, sort_keys=True))
PY
}

contract_check

if "$PLAN_ONLY"; then
    preflight_payload | python3 -c \
        'import json,sys; value=json.load(sys.stdin); value.update(status="plan", install=False); print(json.dumps(value, ensure_ascii=False, sort_keys=True))'
    exit 0
fi

if [[ "$SKIP_DEPENDENCY_CHECK" == false ]]; then
    check_dependencies
fi

if [[ "$CHECK_ONLY" == true ]]; then
    preflight_payload | python3 -c \
        'import json,sys; value=json.load(sys.stdin); value.update(status="preflight", contract="verified"); print(json.dumps(value, ensure_ascii=False, sort_keys=True))'
    exit 0
fi

FREE_BYTES="$(free_bytes)"
REQUIRED_BYTES="$(python3 - "$MIN_FREE_GIB" <<'PY'
import sys
print(int(float(sys.argv[1]) * (1024 ** 3)))
PY
)"
if [[ "$ALLOW_LOW_DISK" == false && "$FREE_BYTES" -lt "$REQUIRED_BYTES" ]]; then
    fail "only $(free_gib) GiB is free; at least ${MIN_FREE_GIB} GiB is recommended (use --allow-low-disk to override)"
fi

mkdir -p "$OUTPUT_DIR" "$MOZ_STATE"

if [[ ! -f "$SOURCE_DIR/_READY" ]]; then
    if [[ -e "$SOURCE_DIR" || -L "$SOURCE_DIR" ]]; then
        fail "incomplete source tree preserved at $SOURCE_DIR; move it aside explicitly before retrying"
    fi
    if [[ ! -f "$TARBALL" ]] || ! xz -t "$TARBALL" 2>/dev/null; then
        if command -v aria2c >/dev/null 2>&1; then
            aria2c -c -x16 -s16 -k1M --dir="$ROOT" -o "$(basename "$TARBALL")" \
                "https://archive.mozilla.org/pub/firefox/releases/${version}/source/firefox-${version}.source.tar.xz"
        else
            curl --fail --location --retry 3 --continue-at - --output "$TARBALL" \
                "https://archive.mozilla.org/pub/firefox/releases/${version}/source/firefox-${version}.source.tar.xz"
        fi
        xz -t "$TARBALL" || fail "source archive integrity check failed; preserved $TARBALL"
    fi
    make setup-minimal version="$version" release="$release" MOZBUILD_STATE_PATH="$MOZ_STATE"
    BUILD_TARGET="macos,arm64" MOZBUILD_STATE_PATH="$MOZ_STATE" \
        make dir version="$version" release="$release" reverse_release="$reverse_release"
fi
[[ -f "$SOURCE_DIR/configure.py" ]] || fail "prepared source tree has no configure.py"
python3 "$ROOT/scripts/inject-trace-to-source.py" "$SOURCE_DIR" \
    --verify --strict --expect-version "${version}-${release}" --expect-hooks 77
python3 "$ROOT/scripts/validate_reverse_build.py" --source-dir "$SOURCE_DIR"
BUILD_TARGET="macos,arm64" MOZBUILD_STATE_PATH="$MOZ_STATE" \
    MOZ_PARALLEL_BUILD="${MOZ_PARALLEL_BUILD:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}" \
    make build version="$version" release="$release" reverse_release="$reverse_release"
BUILD_TARGET="macos,arm64" MOZBUILD_STATE_PATH="$MOZ_STATE" \
    make package-macos version="$version" release="$release" reverse_release="$reverse_release" arch="$ARCH"

PACKAGE_SOURCE="$ROOT/camoufox-${version}-${release}-mac.${ARCH}.zip"
[[ -f "$PACKAGE_SOURCE" ]] || fail "packaging did not produce $PACKAGE_SOURCE"
if [[ "$PACKAGE_SOURCE" != "$ARCHIVE" ]]; then
    cp -f "$PACKAGE_SOURCE" "$ARCHIVE"
fi

python3 "$ROOT/scripts/validate_reverse_build.py" \
    --version "$version" \
    --release "$release" \
    --reverse-release "$reverse_release" \
    --archive "$ARCHIVE"

digest="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
printf '%s  %s\n' "$digest" "$(basename "$ARCHIVE")" > "$CHECKSUM"

if [[ "$INSTALL" == true ]]; then
    python3 "$ROOT/scripts/install-camoufox-reverse.py" \
        "$ARCHIVE" --sha256 "$digest"
fi

if [[ "$KEEP_SOURCE" == false ]]; then
    rm -rf "$SOURCE_DIR"
fi

python3 - "$ARCHIVE" "$CHECKSUM" "$SELECTOR" <<'PY'
import json
import sys
print(json.dumps({
    "status": "built",
    "archive": sys.argv[1],
    "sha256_file": sys.argv[2],
    "selector": sys.argv[3],
}, ensure_ascii=False, sort_keys=True))
PY
