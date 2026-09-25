"""Browser-free contract tests for the pinned reverse browser build script."""

from __future__ import annotations

import json
import lzma
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "build-reverse-browser.sh"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_plan_is_json_and_uses_pinned_reverse_selector():
    result = run_script("--plan")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "plan"
    assert payload["selector"] == "whitenightshadow/152.0.4-beta.30-reverse.9"
    assert payload["target"] == "macos"
    assert payload["arch"] == "arm64"
    assert payload["install"] is False


def test_unknown_target_fails_before_build():
    result = run_script("--plan", "--target", "linux")

    assert result.returncode != 0
    assert "macOS arm64" in result.stderr


def test_check_only_reports_machine_prerequisites_as_json():
    result = run_script("--check-only", "--skip-dependency-check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "preflight"
    assert payload["selector"] == "whitenightshadow/152.0.4-beta.30-reverse.9"
    assert payload["source_dir"].endswith("camoufox-152.0.4-beta.30")


@pytest.fixture
def build_sandbox(tmp_path):
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    shutil.copy2(ROOT / "upstream.sh", root / "upstream.sh")
    # Toolchain compilation is external; exercise real shell control flow.
    (scripts / "validate_reverse_build.py").write_text("pass\n")
    (scripts / "inject-trace-to-source.py").write_text("pass\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    commands = {
        "make": '#!/bin/bash\nprintf "make:%s:%s\\n" "$PWD" "$*" >> "$BUILD_TEST_LOG"\n'
                'if [[ "$1" == setup-minimal ]]; then mkdir -p camoufox-152.0.4-beta.30; touch camoufox-152.0.4-beta.30/configure.py; fi\n'
                'if [[ "$1" == dir ]]; then touch camoufox-152.0.4-beta.30/_READY; fi\n'
                'if [[ "$1" == package-macos ]]; then touch camoufox-152.0.4-beta.30-mac.arm64.zip; fi\n',
        "aria2c": '#!/bin/bash\nprintf "download:%s:%s\\n" "$PWD" "$*" >> "$BUILD_TEST_LOG"\n',
        "curl": '#!/bin/bash\nexit 91\n',
    }
    for name, content in commands.items():
        executable = binaries / name
        executable.write_text(content)
        executable.chmod(0o755)
    source = root / "camoufox-152.0.4-beta.30"
    archive = root / "firefox-152.0.4.source.tar.xz"
    archive.write_bytes(lzma.compress(b"test archive"))

    def run(*args):
        return subprocess.run(
            ["bash", str(scripts / SCRIPT.name), "--skip-dependency-check", "--allow-low-disk", *args],
            cwd=tmp_path,
            env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                 "BUILD_TEST_LOG": str(log)},
            capture_output=True, text=True, check=False,
        )

    return root, source, archive, log, run


def test_incomplete_source_is_preserved(build_sandbox):
    _, source, _, log, run = build_sandbox
    source.mkdir()
    evidence = source / "unfinished.patch"
    evidence.write_text("keep this interruption evidence")

    result = run()

    assert evidence.is_file(), "must not delete an incomplete source tree"
    assert result.returncode != 0
    assert "incomplete source tree" in result.stderr
    assert not log.exists(), "must fail before downloading or patching"


def test_corrupt_archive_blocks_extraction(build_sandbox):
    _, source, archive, log, run = build_sandbox
    archive.write_bytes(b"interrupted download")

    result = run()

    assert result.returncode != 0
    assert "source archive integrity" in result.stderr
    assert not source.exists()
    assert "make:" not in (log.read_text() if log.exists() else "")


def test_ready_source_skips_setup_from_another_working_directory(build_sandbox):
    root, source, _, log, run = build_sandbox
    source.mkdir()
    (source / "_READY").touch()
    (source / "configure.py").touch()

    result = run("--output-dir", "output", "--mozbuild-state", "state")

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert calls[0].startswith(f"make:{root}:build ")
    assert calls[1].startswith(f"make:{root}:package-macos ")
    assert (root.parent / "output" / "camoufox-152.0.4-beta.30-mac.arm64.zip.sha256").is_file()


def test_existing_valid_archive_is_reused(build_sandbox):
    root, source, _, log, run = build_sandbox

    result = run()

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert all(call.startswith(f"make:{root}:") for call in calls)
    assert [call.split(":", 2)[2].split()[0] for call in calls] == [
        "setup-minimal", "dir", "build", "package-macos",
    ]
    assert (source / "_READY").is_file()


def test_ready_marker_without_source_blocks_build(build_sandbox):
    _, source, _, log, run = build_sandbox
    source.mkdir()
    (source / "_READY").touch()

    result = run()

    assert result.returncode != 0
    assert "configure.py" in result.stderr
    assert not log.exists()


def test_stale_native_hooks_block_build_without_repatching(build_sandbox):
    root, source, _, log, run = build_sandbox
    source.mkdir()
    (source / "_READY").touch()
    (source / "configure.py").touch()
    (root / "scripts/inject-trace-to-source.py").write_text("raise SystemExit(7)\n")

    result = run()

    assert result.returncode == 7
    assert not log.exists()
    assert (source / "_READY").exists()
