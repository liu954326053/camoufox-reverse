#!/usr/bin/env python3
"""Validate the pinned reverse browser contract and packaged archives."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "pythonlib"))

from camoufox.reverse_compat import (  # noqa: E402
    BROWSER_SELECTOR,
    REVERSE_RELEASE,
    UPSTREAM_RELEASE,
    UPSTREAM_VERSION,
    capability_contract,
)


def _read_upstream_assignments() -> dict[str, str]:
    assignments: dict[str, str] = {}
    pattern = re.compile(r"^(version|release|reverse_release)=(\S+)$")
    for raw_line in (ROOT / "upstream.sh").read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(raw_line.strip())
        if match:
            assignments[match.group(1)] = match.group(2)
    return assignments


def _expected_contract() -> dict[str, object]:
    assignments = _read_upstream_assignments()
    expected = {
        "version": UPSTREAM_VERSION,
        "release": UPSTREAM_RELEASE,
        "reverse_release": REVERSE_RELEASE,
    }
    if assignments != expected:
        raise ValueError(
            "upstream.sh does not match the pinned reverse contract: "
            f"expected {expected}, got {assignments}"
        )
    contract = capability_contract()
    capabilities_path = ROOT / "settings" / "camoufox-reverse-capabilities.json"
    capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
    if capabilities != contract:
        raise ValueError(
            "settings/camoufox-reverse-capabilities.json does not match "
            "pythonlib/camoufox/reverse_compat.py"
        )
    if contract["browser_selector"] != BROWSER_SELECTOR:
        raise ValueError("browser selector is inconsistent")
    return contract


def _archive_executable(name: str) -> str:
    if "-lin." in name:
        return "camoufox-bin"
    if "-mac." in name:
        return "Camoufox.app/Contents/MacOS/camoufox"
    if "-win." in name:
        return "camoufox.exe"
    raise ValueError(f"unrecognized reverse archive name: {name}")


def _validate_source(path: Path) -> None:
    for relative in ("camoucfg/PropertyTracer.cpp", "camoucfg/PropertyTracer.hpp", "juggler/NetworkObserver.js",
                     "juggler/content/FrameTree.js"):
        if (path / relative).read_bytes() != (ROOT / "additions" / relative).read_bytes():
            raise ValueError(f"prepared source is stale: {relative}; preserve the tree and refresh it before building")
    shutdown = (path / "xpcom/base/AppShutdown.cpp").read_text(encoding="utf-8")
    entry = re.search(r"void AppShutdown::DoImmediateExit\(int aExitCode\)\s*\{(.*?)#ifdef XP_WIN", shutdown, re.S)
    if ('#include "PropertyTracer.hpp"' not in shutdown or entry is None
            or "camou::PropertyTracer::Instance().Shutdown();" not in entry.group(1)):
        raise ValueError("prepared source is missing the immediate-exit trace drain patch")


def _validate_archive(path: Path, contract: dict[str, object]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = {item.filename.rstrip("/") for item in archive.infolist()}
        required = {"version.json", "camoufox-reverse-capabilities.json", _archive_executable(path.name)}
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"{path.name} is missing archive entries: {missing}")
        version = json.loads(archive.read("version.json"))
        capabilities = json.loads(archive.read("camoufox-reverse-capabilities.json"))
    expected_version = {"version": UPSTREAM_VERSION, "release": UPSTREAM_RELEASE}
    if version != expected_version:
        raise ValueError(f"{path.name} has invalid version.json: {version}")
    if capabilities != contract:
        raise ValueError(f"{path.name} has an inconsistent capability marker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", type=Path, default=[])
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--release")
    parser.add_argument("--reverse-release")
    args = parser.parse_args(argv)
    try:
        contract = _expected_contract()
        supplied = {
            "version": args.version,
            "release": args.release,
            "reverse_release": args.reverse_release,
        }
        for key, value in supplied.items():
            if value is not None:
                expected = {
                    "version": UPSTREAM_VERSION,
                    "release": UPSTREAM_RELEASE,
                    "reverse_release": REVERSE_RELEASE,
                }[key]
                if value != expected:
                    raise ValueError(f"{key} mismatch: expected {expected}, got {value}")
        for archive in args.archive:
            _validate_archive(archive.resolve(), contract)
        if args.source_dir is not None:
            _validate_source(args.source_dir.resolve())
        print(json.dumps({
            "status": "verified",
            "upstream_version": contract["upstream_version"],
            "browser_selector": contract["browser_selector"],
            "reverse_release": contract["reverse_release"],
            "archives": [str(path.resolve()) for path in args.archive],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
