#!/usr/bin/env python3
"""Testable helpers for the reverse.9 zero-repo bootstrap installer.

`install.sh` in the agent bootstrap bundle is a thin shell wrapper; the
platform mapping, release URL building and SHA256SUMS parsing live here so
scripts/tests can cover them without touching the network.
"""

from __future__ import annotations

import argparse
import platform
import re
import sys

RELEASE_TAG = "v152.0.4-beta.30-reverse.9"
RELEASE_REPO = "liu954326053/camoufox-reverse"
RELEASE_BASE = (
    f"https://github.com/{RELEASE_REPO}/releases/download/{RELEASE_TAG}"
)
UPSTREAM_VERSION_STRING = "152.0.4-beta.30"
BROWSER_FOLDER = f"{UPSTREAM_VERSION_STRING}-reverse.9"
BROWSER_REPO_NAME = "whitenightshadow"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# uname -s / uname -m -> (asset platform.arch). Mirrors the build matrix
# exclusions in .github/workflows/build.yml.
_PLATFORM_MAP = {
    ("linux", "x86_64"): "lin.x86_64",
    ("linux", "amd64"): "lin.x86_64",
    ("darwin", "x86_64"): "mac.x86_64",
    ("darwin", "arm64"): "mac.arm64",
    ("windows", "x86_64"): "win.x86_64",
    ("windows", "amd64"): "win.x86_64",
    ("msys", "x86_64"): "win.x86_64",
    ("mingw", "x86_64"): "win.x86_64",
    ("cygwin", "x86_64"): "win.x86_64",
}


class BootstrapError(RuntimeError):
    """The local platform or release metadata cannot support installation."""


def asset_platform(system: str | None = None, machine: str | None = None) -> str:
    """Map uname values to the release asset platform suffix."""
    system = (system or platform.system()).strip().lower()
    machine = (machine or platform.machine()).strip().lower()
    # MSYS2/MinGW/Cygwin report names like "MINGW64_NT-10.0".
    for prefix in ("msys", "mingw", "cygwin"):
        if system.startswith(prefix):
            system = prefix
            break
    key = (system, machine)
    try:
        return _PLATFORM_MAP[key]
    except KeyError:
        raise BootstrapError(
            f"不支持的平台组合: {system}/{machine}。"
            "reverse.9 只发布 lin.x86_64、mac.x86_64、mac.arm64、win.x86_64。"
        ) from None


def asset_name(system: str | None = None, machine: str | None = None) -> str:
    """Return the browser asset filename for this platform."""
    return f"camoufox-{UPSTREAM_VERSION_STRING}-{asset_platform(system, machine)}.zip"


def asset_url(name: str, base: str = RELEASE_BASE) -> str:
    if not name.startswith("camoufox-") or not name.endswith(".zip"):
        raise BootstrapError(f"非法浏览器资产名: {name}")
    return f"{base.rstrip('/')}/{name}"


def sums_url(base: str = RELEASE_BASE) -> str:
    return f"{base.rstrip('/')}/SHA256SUMS"


def expected_sha256(sums_text: str, name: str) -> str:
    """Extract the expected SHA256 for an asset from SHA256SUMS content."""
    matches: list[str] = []
    for line in sums_text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, separator, entry = line.partition(" ")
        if not separator:
            raise BootstrapError(f"SHA256SUMS 行格式非法: {line!r}")
        entry = entry.strip().lstrip("*")
        if entry == name:
            matches.append(digest)
    if not matches:
        raise BootstrapError(f"SHA256SUMS 中找不到资产 {name}")
    if len(set(matches)) != 1:
        raise BootstrapError(f"SHA256SUMS 中 {name} 的哈希不一致")
    digest = matches[0]
    if not SHA256_RE.fullmatch(digest):
        raise BootstrapError(f"SHA256SUMS 中 {name} 的哈希格式非法: {digest!r}")
    return digest.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=RELEASE_BASE,
        help="release 下载基地址（默认指向 GitHub Release，可用 file:// 覆盖测试）",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("asset-name", help="打印本机平台对应的浏览器资产名")
    sub.add_parser("sums-url", help="打印 SHA256SUMS 的下载地址")
    asset = sub.add_parser("asset-url", help="打印浏览器资产的下载地址")
    asset.add_argument("name")
    expected = sub.add_parser("expected-sha256", help="从 SHA256SUMS 文件取资产期望哈希")
    expected.add_argument("sums_file")
    expected.add_argument("name")
    args = parser.parse_args(argv)
    try:
        if args.action == "asset-name":
            print(asset_name())
        elif args.action == "sums-url":
            print(sums_url(args.base))
        elif args.action == "asset-url":
            print(asset_url(args.name, args.base))
        else:
            with open(args.sums_file, encoding="utf-8") as handle:
                print(expected_sha256(handle.read(), args.name))
        return 0
    except BootstrapError as error:
        print(f"bootstrap 错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
