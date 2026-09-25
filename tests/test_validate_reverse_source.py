"""Reject a stale prepared tree before it can be labeled as a new release."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def prepared_source(tmp_path):
    for name in ("camoucfg/PropertyTracer.cpp", "camoucfg/PropertyTracer.hpp", "juggler/NetworkObserver.js",
                 "juggler/content/FrameTree.js"):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "additions" / name, dest)
    shutdown = tmp_path / "xpcom/base/AppShutdown.cpp"
    shutdown.parent.mkdir(parents=True)
    shutdown.write_text('#include "PropertyTracer.hpp"\nvoid AppShutdown::DoImmediateExit(int aExitCode) {\n'
                        'camou::PropertyTracer::Instance().Shutdown();\n#ifdef XP_WIN\n#endif\n}\n')
    return tmp_path


def validate(source):
    return subprocess.run(["python3", str(ROOT / "scripts/validate_reverse_build.py"), "--source-dir", str(source)],
                          text=True, capture_output=True, check=False)


def test_current_native_inputs_are_accepted(prepared_source):
    result = validate(prepared_source)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("relative", ["camoucfg/PropertyTracer.cpp", "juggler/content/FrameTree.js"])
def test_stale_prepared_tracer_is_rejected(prepared_source, relative):
    (prepared_source / relative).write_text("stale implementation")
    result = validate(prepared_source)
    assert result.returncode != 0
    assert relative in result.stdout


def test_missing_immediate_exit_drain_is_rejected(prepared_source):
    shutdown = prepared_source / "xpcom/base/AppShutdown.cpp"
    shutdown.write_text(shutdown.read_text().replace("camou::PropertyTracer::Instance().Shutdown();", ""))
    result = validate(prepared_source)
    assert result.returncode != 0
    assert "immediate-exit" in result.stdout
