import pytest
from pathlib import Path
from camoufox_reverse_mcp import browser as browser_module
from camoufox_reverse_mcp.browser import BrowserManager


def test_browser_manager_init():
    mgr = BrowserManager()
    assert mgr.browser is None
    assert mgr.active_page_name is None
    assert len(mgr.contexts) == 0
    assert len(mgr.pages) == 0
    assert mgr._capturing is False


def test_default_config():
    assert isinstance(BrowserManager.default_config, dict)


def test_console_logs_maxlen():
    mgr = BrowserManager()
    assert mgr._console_logs.maxlen == 2000


def test_network_requests_maxlen():
    mgr = BrowserManager()
    assert mgr._network_requests.maxlen == 2000


def test_persistent_scripts_init():
    mgr = BrowserManager()
    assert isinstance(mgr._persistent_scripts, list)
    assert len(mgr._persistent_scripts) == 0


def test_persistent_traces_init():
    mgr = BrowserManager()
    assert isinstance(mgr._persistent_traces, dict)
    assert len(mgr._persistent_traces) == 0


def test_capture_body_default():
    mgr = BrowserManager()
    assert mgr._capture_body is False


def test_init_scripts_list():
    mgr = BrowserManager()
    assert isinstance(mgr._init_scripts, list)
    assert len(mgr._init_scripts) == 0


class _FakeSession:
    session_id = "session-task8"

    def __init__(self, root):
        self.path = root
        self.path.mkdir(parents=True, exist_ok=True)
        self.trace_dir = root / "trace"
        self.manifest_path = root / "manifest.json"

    def manifest_snapshot(self):
        return {"status": "running", "session_id": self.session_id}


class _FakePage:
    url = "about:blank"

    def on(self, *_args):
        return None


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage()]


class _FakeBrowser:
    def __init__(self):
        self.contexts = [_FakeContext()]


class _FakeRuntime:
    instances = []

    def __init__(self, project_dir, **kwargs):
        self.project_dir = project_dir
        self.kwargs = kwargs
        self.session = _FakeSession(project_dir / "runs" / "session-task8")
        self.context = _FakeContext()
        self.page = self.context.pages[0]
        self.browser = None
        self.store = object()
        self.files = {}
        self.closed = []
        type(self).instances.append(self)

    async def __aenter__(self):
        self.browser = _FakeBrowser()
        return self

    async def close(self, incomplete=False):
        self.closed.append(incomplete)
        return {
            "status": "closed",
            "session_id": self.session.session_id,
            "session_dir": str(self.session.path),
            "artifacts": {},
        }

    def write_artifact(self, relative_path, data, kind):
        path = self.session.path / relative_path
        if path.exists() or relative_path in self.files:
            raise ValueError("artifact already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[relative_path] = {"kind": kind, "data": bytes(data)}
        return path


@pytest.mark.asyncio
async def test_launch_uses_one_project_scoped_runtime_without_lazy_launch(tmp_path):
    mgr = BrowserManager(runtime_factory=_FakeRuntime)

    with pytest.raises(RuntimeError, match="launch_browser"):
        await mgr.get_active_page()

    result = await mgr.launch(
        {
            "project_dir": tmp_path / "project",
            "proxy": "http://127.0.0.1:7890",
            "browser_version": "reverse-test",
            "trace_profile": "targeted",
            "enable_trace": True,
        }
    )

    assert result["status"] == "launched"
    runtime = _FakeRuntime.instances[-1]
    assert runtime.project_dir == tmp_path / "project"
    assert runtime.kwargs["proxy"] == "http://127.0.0.1:7890"
    assert runtime.kwargs["browser_version"] == "reverse-test"
    assert runtime.kwargs["trace_profile"] == "targeted"
    assert runtime.kwargs["enable_trace"] is True
    assert runtime.kwargs["os"] == browser_module.detect_host_os()
    assert runtime.kwargs["locale"] == browser_module.detect_system_locale()

    closed = await mgr.close()
    assert closed["status"] == "closed"
    assert runtime.closed == [False]
    assert (await mgr.close())["status"] == "already_closed"


@pytest.mark.asyncio
async def test_manager_rejects_artifact_traversal(tmp_path):
    mgr = BrowserManager(runtime_factory=_FakeRuntime)
    await mgr.launch({"project_dir": tmp_path / "project"})

    with pytest.raises(ValueError, match="traversal"):
        mgr.write_artifact("../outside.bin", b"raw", "fixture", default="raw/fixture.bin")

    await mgr.close()


def test_detect_system_locale_maps_c_locale_to_supported_default(monkeypatch):
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "POSIX")
    monkeypatch.setenv("LC_MESSAGES", "C")
    assert browser_module.detect_system_locale() == "en-US"


@pytest.mark.asyncio
async def test_explicit_os_and_locale_are_preserved(tmp_path):
    mgr = BrowserManager(runtime_factory=_FakeRuntime)
    await mgr.launch(
        {
            "project_dir": tmp_path / "project",
            "os": "macos",
            "locale": "en-US",
        }
    )
    runtime = _FakeRuntime.instances[-1]
    assert runtime.kwargs["os"] == "macos"
    assert runtime.kwargs["locale"] == "en-US"
    await mgr.close()
