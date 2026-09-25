import os
import pytest
from pathlib import Path
from camoufox_reverse_mcp.utils.js_helpers import render_trace_template
from camoufox_reverse_mcp.utils.response_fmt import format_response, truncate_str


def test_render_trace_template():
    js = render_trace_template(
        function_path="window.encrypt",
        max_captures=10,
        log_args=True,
        log_return=False,
        log_stack=True,
    )
    assert "window.encrypt" in js
    assert "10" in js
    assert "true" in js


def test_render_trace_template_defaults():
    js = render_trace_template(function_path="JSON.stringify")
    assert "JSON.stringify" in js
    assert "50" in js


def test_format_response_dict():
    result = format_response({"key": "value"})
    assert '"key"' in result
    assert '"value"' in result


def test_format_response_truncation():
    data = "x" * 100000
    result = format_response(data, max_length=100)
    assert "truncated" in result


def test_truncate_str_short():
    assert truncate_str("hello", 10) == "hello"


def test_truncate_str_long():
    result = truncate_str("a" * 100, 50)
    assert len(result) < 100
    assert "chars total" in result


def test_render_persistent_trace_template():
    from camoufox_reverse_mcp.utils.js_helpers import render_persistent_trace_template
    js = render_persistent_trace_template(
        function_path="XMLHttpRequest.prototype.open",
        max_captures=20,
        log_args=True,
        log_return=True,
        log_stack=True,
    )
    assert "XMLHttpRequest.prototype.open" in js
    assert "__MCP_TRACE__" in js
    assert "20" in js


def test_hook_files_exist():
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    expected_files = [
        "xhr_hook.js",
        "fetch_hook.js",
        "crypto_hook.js",
        "websocket_hook.js",
        "debugger_trap.js",
        "trace_template.js",
        "trace_persistent_template.js",
        "property_access_hook.js",
        "jsvmp_hook.js",
    ]
    for f in expected_files:
        assert os.path.exists(os.path.join(hooks_dir, f)), f"Missing hook file: {f}"


@pytest.mark.asyncio
async def test_network_capture_is_session_scoped_and_raw(monkeypatch, tmp_path):
    from camoufox_reverse_mcp.browser import BrowserManager
    from camoufox_reverse_mcp.tools import network
    from tests.fakes import make_fake_runtime

    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch({"project_dir": tmp_path / "project"})
    monkeypatch.setattr(network, "browser_manager", manager)

    result = await network.network_capture(
        action="start", url_pattern="**/api/**", capture_body=True
    )
    assert result["status"] == "started"
    assert result["capture_profile"] == "raw"
    artifact = Path(result["artifacts"]["capture"])
    assert artifact.is_relative_to(Path(result["session_dir"]))
    assert "api" in artifact.read_text(encoding="utf-8")

    await manager.close()


@pytest.mark.asyncio
async def test_environment_reports_session_paths_and_writes_raw_status(monkeypatch, tmp_path):
    from camoufox_reverse_mcp.browser import BrowserManager
    from camoufox_reverse_mcp.tools import environment
    from tests.fakes import make_fake_runtime

    manager = BrowserManager(runtime_factory=make_fake_runtime)
    await manager.launch({"project_dir": tmp_path / "project"})
    monkeypatch.setattr(environment, "browser_manager", manager)

    result = await environment.check_environment()
    assert result["session_id"] == manager.session.session_id
    status_path = Path(result["artifacts"]["environment"])
    assert status_path.is_relative_to(Path(result["session_dir"]))
    assert "cache_dir" not in result["camoufox_reverse"]
    assert "session_id" in status_path.read_text(encoding="utf-8")

    await manager.close()


@pytest.mark.asyncio
async def test_launch_browser_raw_profile_and_close_error_envelopes(monkeypatch, tmp_path):
    from camoufox_reverse_mcp.browser import BrowserManager
    from camoufox_reverse_mcp.tools import navigation
    from tests.fakes import make_fake_runtime

    manager = BrowserManager(runtime_factory=make_fake_runtime)
    monkeypatch.setattr(navigation, "browser_manager", manager)
    project = tmp_path / "project"
    project.mkdir()

    launched = await navigation.launch_browser(str(project), capture_profile="raw")
    assert launched["status"] == "launched"
    assert launched["capture_profile"] == "raw"
    session_dir = Path(launched["session_dir"])
    assert Path(launched["artifacts"]["manifest"]).is_relative_to(session_dir)
    assert Path(launched["artifacts"]["trace_dir"]).is_relative_to(session_dir)

    closed = await navigation.close_browser()
    assert closed["status"] == "closed"
    assert Path(closed["session_dir"]).resolve() == session_dir.resolve()
    assert (await navigation.close_browser())["status"] == "already_closed"

    from camoufox_reverse_mcp.property_trace import active_trace_dir
    assert active_trace_dir() is None

    invalid_profile = await navigation.launch_browser(
        str(project), capture_profile="metadata_only"
    )
    assert invalid_profile["status"] == "error"
    assert invalid_profile["error"]["code"] == "launch_failed"
    assert "session_dir" not in invalid_profile

    relative = await navigation.launch_browser("relative/project")
    assert relative["status"] == "error"
    assert relative["error"]["code"] == "launch_failed"
