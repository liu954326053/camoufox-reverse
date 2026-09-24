import asyncio
import gc
import json
import weakref
from pathlib import Path

import pytest

from camoufox import reverse_runtime


class FakeSession:
    def __init__(self, root: Path):
        self.session_id = "session-fixture"
        self.path = root
        self.raw_dir = root / "raw"
        self.trace_dir = root / "trace"
        self.manifest_path = root / "manifest.json"
        self.raw_dir.mkdir(parents=True)
        self.trace_dir.mkdir()
        self.manifest_path.write_text('{"status":"running"}\n')
        self.closed = []

    def mark_incomplete(self, error=None):
        self.closed.append("incomplete")
        self.manifest_path.write_text('{"status":"incomplete"}\n')

    def manifest_snapshot(self):
        return json.loads(self.manifest_path.read_text())


class FakeStore:
    def __init__(self, session):
        self.session = session
        self.files = {}
        self.finalized = False
        self.flushed = False

    def append_bytes(self, path, data):
        self.files[path] = bytes(data)
        target = self.session.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    def register_artifact(self, path, sha256, size, kind):
        return {"path": path, "sha256": sha256, "size": size, "kind": kind}

    def finalize(self):
        self.finalized = True
        return self.session.path / "index.json"

    def rebuild_index(self):
        return self.session.path / "index.json"

    def flush(self):
        self.flushed = True


class FakePage:
    def __init__(self):
        self.handlers = {}
        self.url = "about:blank"
        self.frames = []

    def on(self, name, handler):
        self.handlers[name] = handler


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]
        self.handlers = {}

    def on(self, name, handler):
        self.handlers[name] = handler

    async def storage_state(self):
        return {"cookies": [{"name": "raw-cookie", "value": "fixture"}], "origins": []}


class FakeBrowser:
    def __init__(self):
        self.contexts = [FakeContext()]


class FakeCamoufox:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.exited = False
        type(self).instances.append(self)

    async def __aenter__(self):
        self.browser = FakeBrowser()
        return self.browser

    async def __aexit__(self, *args):
        self.exited = True
        return None


@pytest.mark.asyncio
async def test_runtime_owns_browser_and_finalizes_session(monkeypatch, tmp_path):
    session = FakeSession(tmp_path / "session")
    store = FakeStore(session)
    monkeypatch.setattr(
        reverse_runtime,
        "reverse_launch_options",
        lambda **kwargs: ({"env": {}}, session),
    )
    monkeypatch.setattr(reverse_runtime, "EvidenceStore", lambda value: store)
    monkeypatch.setattr(reverse_runtime, "AsyncCamoufox", FakeCamoufox)

    runtime = reverse_runtime.AsyncReverseBrowser(tmp_path / "project", enable_trace=False)
    entered = await runtime.__aenter__()
    assert entered is runtime
    assert runtime.page.url == "about:blank"

    artifact = runtime.write_artifact("raw/manual.bin", b"raw-value", "fixture")
    assert artifact.name == "manual.bin"
    result = await runtime.close()

    assert result["status"] == "closed"
    assert result["session_id"] == session.session_id
    assert store.finalized is True
    assert store.files["raw/manual.bin"] == b"raw-value"


@pytest.mark.asyncio
async def test_runtime_passes_trace_switch_to_launch_options(monkeypatch, tmp_path):
    session = FakeSession(tmp_path / "session")
    captured = {}

    def fake_reverse_launch_options(**kwargs):
        captured.update(kwargs)
        return {"env": {}}, session

    monkeypatch.setattr(reverse_runtime, "reverse_launch_options", fake_reverse_launch_options)
    monkeypatch.setattr(reverse_runtime, "EvidenceStore", lambda value: FakeStore(value))
    monkeypatch.setattr(reverse_runtime, "AsyncCamoufox", FakeCamoufox)

    runtime = reverse_runtime.AsyncReverseBrowser(
        tmp_path / "project", enable_trace=False
    )
    await runtime.__aenter__()
    await runtime.close()

    assert captured["enable_trace"] is False


@pytest.mark.asyncio
async def test_runtime_closes_entered_browser_when_context_setup_fails(
    monkeypatch, tmp_path
):
    session = FakeSession(tmp_path / "session")

    class FailingContext:
        @property
        def pages(self):
            raise RuntimeError("page setup failed")

    class FailingCamoufox(FakeCamoufox):
        async def __aenter__(self):
            self.browser = FakeBrowser()
            self.browser.contexts = [FailingContext()]
            return self.browser

    monkeypatch.setattr(
        reverse_runtime,
        "reverse_launch_options",
        lambda **kwargs: ({"env": {}}, session),
    )
    monkeypatch.setattr(reverse_runtime, "EvidenceStore", lambda value: FakeStore(value))
    monkeypatch.setattr(reverse_runtime, "AsyncCamoufox", FailingCamoufox)

    runtime = reverse_runtime.AsyncReverseBrowser(tmp_path / "project")
    with pytest.raises(RuntimeError, match="page setup failed"):
        await runtime.__aenter__()

    assert FailingCamoufox.instances[-1].exited is True
    assert session.closed == ["incomplete"]


@pytest.mark.asyncio
async def test_runtime_marks_session_incomplete_when_browser_start_fails(monkeypatch, tmp_path):
    session = FakeSession(tmp_path / "session")

    class FailingCamoufox(FakeCamoufox):
        async def __aenter__(self):
            raise RuntimeError("browser failed")

    monkeypatch.setattr(
        reverse_runtime,
        "reverse_launch_options",
        lambda **kwargs: ({"env": {}}, session),
    )
    monkeypatch.setattr(reverse_runtime, "EvidenceStore", lambda value: FakeStore(value))
    monkeypatch.setattr(reverse_runtime, "AsyncCamoufox", FailingCamoufox)

    runtime = reverse_runtime.AsyncReverseBrowser(tmp_path / "project")
    with pytest.raises(RuntimeError, match="browser failed"):
        await runtime.__aenter__()
    assert session.closed == ["incomplete"]


def test_runtime_rejects_overwrite_and_outside_artifacts(monkeypatch, tmp_path):
    session = FakeSession(tmp_path / "session")
    store = FakeStore(session)
    monkeypatch.setattr(reverse_runtime, "EvidenceStore", lambda value: store)
    runtime = reverse_runtime.AsyncReverseBrowser.__new__(reverse_runtime.AsyncReverseBrowser)
    runtime.session = session
    runtime.store = store

    runtime.write_artifact("raw/one.bin", b"one", "fixture")
    with pytest.raises(ValueError):
        runtime.write_artifact("raw/one.bin", b"again", "fixture")
    with pytest.raises(ValueError):
        runtime.write_artifact("../outside.bin", b"bad", "fixture")


@pytest.fixture
def runtime_factory(monkeypatch, tmp_path):
    def create():
        session = FakeSession(tmp_path / "capture")
        store = FakeStore(session)
        monkeypatch.setattr(reverse_runtime, "reverse_launch_options", lambda **kw: ({}, session))
        monkeypatch.setattr(reverse_runtime, "EvidenceStore", lambda value: store)
        monkeypatch.setattr(reverse_runtime, "AsyncCamoufox", FakeCamoufox)
        return reverse_runtime.AsyncReverseBrowser(tmp_path / "project", enable_trace=False)
    return create


class FakeRequest:
    url = "https://fixture.test/same"
    method = "POST"
    resource_type = "fetch"
    headers = {"content-type": "application/octet-stream"}
    post_data_buffer = b"\xff\x00\x80"
    failure = "NS_ERROR_CONNECTION_REFUSED"
    redirected_from = None
    redirected_to = None

    @property
    def post_data(self):
        raise AssertionError("must not read a decoded request body")

    async def headers_array(self):
        return [{"name": "Cookie", "value": "secret=raw"},
                {"name": "X-Duplicate", "value": "one"},
                {"name": "X-Duplicate", "value": "two"}]


class FakeResponse:
    status = 200
    headers = {"content-type": "application/octet-stream"}

    def __init__(self, request, body=b"\x80\x00\xff"):
        self.request = request
        self.payload = body

    async def headers_array(self):
        return [{"name": "Set-Cookie", "value": "a=1; HttpOnly"},
                {"name": "Set-Cookie", "value": "b=2; Secure"}]

    async def body(self):
        return self.payload


def metadata(runtime):
    latest = {}
    for path, data in runtime.store.files.items():
        if "/metadata" in path and path.endswith(".json"):
            row = json.loads(data)
            latest[row["id"]] = row
    return list(latest.values())


@pytest.mark.asyncio
async def test_completed_task_failure_cannot_finalize_complete(runtime_factory):
    runtime = await runtime_factory().__aenter__()

    async def fail():
        raise RuntimeError("capture failed before drain")

    runtime._spawn(fail())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    result = await runtime.close()
    assert result["status"] == "incomplete"
    assert runtime.session.manifest_snapshot()["status"] == "incomplete"
    assert runtime._cm.exited
    assert "capture failed before drain" in json.dumps(result)


@pytest.mark.asyncio
async def test_request_identity_retains_object_and_ignores_duplicate_events(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    request = FakeRequest()
    reference = weakref.ref(request)
    runtime._on_request(request)
    runtime._on_request(request)
    runtime._on_request_failed(request)
    del request
    gc.collect()
    assert reference() is not None
    await runtime.drain()
    assert len(metadata(runtime)) == 1
    assert (await runtime.close())["status"] == "closed"


@pytest.mark.asyncio
async def test_binary_read_failures_never_fall_back_to_text(runtime_factory):
    runtime = await runtime_factory().__aenter__()

    class Request(FakeRequest):
        @property
        def post_data_buffer(self):
            raise RuntimeError("binary request unavailable")

    class Response(FakeResponse):
        async def body(self):
            raise RuntimeError("binary response unavailable")

        async def text(self):
            pytest.fail("must not use response text")

    request = Request()
    runtime._on_request(request)
    runtime._on_response(Response(request))
    result = await runtime.close()
    row = metadata(runtime)[0]
    assert row["request_body_error"] == "binary request unavailable"
    assert row["response_body_error"] == "binary response unavailable"
    assert not any(path.endswith(".body") for path in runtime.store.files)
    assert result["status"] == "incomplete"


@pytest.mark.asyncio
async def test_empty_binary_body_is_preserved(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    request = FakeRequest()
    request.post_data_buffer = b""
    runtime._on_request(request)
    runtime._on_response(FakeResponse(request, b""))
    await runtime.drain()
    row = metadata(runtime)[0]
    assert runtime.store.files[f'raw/network/{row["id"]}/request.body'] == b""
    assert runtime.store.files[f'raw/network/{row["id"]}/response.body'] == b""
    await runtime.close()


@pytest.mark.asyncio
async def test_page_only_context_fallback_remains_usable(runtime_factory, monkeypatch):
    runtime = runtime_factory()
    monkeypatch.setattr(FakeContext, "on", None)
    await runtime.__aenter__()
    request = FakeRequest()
    runtime.page.handlers["request"](request)
    runtime.page.handlers["requestfailed"](request)
    await runtime.drain()
    assert metadata(runtime)[0]["failure"] == "NS_ERROR_CONNECTION_REFUSED"
    result = await runtime.close()
    assert "popup/worker coverage unavailable" in json.dumps(result)


@pytest.mark.asyncio
async def test_snapshot_preserves_local_storage_and_multiple_pages(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()

    async def state():
        return {"cookies": [], "origins": [{"origin": "https://fixture.test",
                "localStorage": [{"name": "key", "value": "raw"}]}]}

    class Frame:
        url = "https://fixture.test"

        async def evaluate(self, script):
            return {"session": "raw"}

    monkeypatch.setattr(runtime.context, "storage_state", state)
    runtime.context.pages.append(FakePage())
    for page in runtime.context.pages:
        page.frames = [Frame()]
    snapshot = json.loads(Path((await runtime.snapshot())["path"]).read_bytes())
    assert snapshot["origins"][0]["localStorage"] == [{"name": "key", "value": "raw"}]
    assert [item["page"] for item in snapshot["sessionStorage"]] == [0, 1]
    assert (await runtime.close())["status"] == "closed"


@pytest.mark.asyncio
async def test_real_store_failed_index_still_marks_manifest_incomplete(monkeypatch, tmp_path):
    from camoufox.reverse_project import ReverseProject

    session = ReverseProject.open(tmp_path / "project").create_session()
    monkeypatch.setattr(reverse_runtime, "reverse_launch_options", lambda **kw: ({}, session))
    monkeypatch.setattr(reverse_runtime, "AsyncCamoufox", FakeCamoufox)
    runtime = await reverse_runtime.AsyncReverseBrowser(
        tmp_path / "project", enable_trace=False
    ).__aenter__()

    def fail(*args):
        raise reverse_runtime.EvidenceError("disk index failed")

    monkeypatch.setattr(runtime.store, "_write_index", fail)
    result = await runtime.close()
    assert result["status"] == "incomplete"
    assert session.manifest_snapshot()["status"] == "incomplete"
    assert runtime._cm.exited


@pytest.mark.asyncio
async def test_unpersistable_status_is_not_reported_as_success(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()

    def fail(*args, **kwargs):
        raise RuntimeError("all writes failed")

    monkeypatch.setattr(runtime.store, "finalize", fail)
    monkeypatch.setattr(runtime.session, "mark_incomplete", fail)
    monkeypatch.setattr(runtime.session, "_close_manifest", fail, raising=False)
    with pytest.raises(reverse_runtime.EvidenceError, match="status could not be persisted"):
        await runtime.close()
    assert runtime._cm.exited


@pytest.mark.asyncio
async def test_cancelled_close_still_drains_and_marks_incomplete(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()
    started = asyncio.Event()

    async def state():
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.context, "storage_state", state)
    closing = asyncio.create_task(runtime.close())
    await started.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert runtime._cm.exited
    assert runtime.session.manifest_snapshot()["status"] == "incomplete"


@pytest.mark.asyncio
async def test_same_url_requests_have_distinct_bytes_and_full_headers(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    first, second = FakeRequest(), FakeRequest()
    runtime._on_request(first)
    runtime._on_request(second)
    runtime._on_response(FakeResponse(second, b"second\xff"))
    runtime._on_response(FakeResponse(first, b"first\x80"))
    await runtime.drain()
    records = metadata(runtime)
    assert len(records) == 2
    assert len({row["id"] for row in records}) == 2
    for row in records:
        assert row["request_headers"] == await first.headers_array()
        assert row["response_headers"] == await FakeResponse(first).headers_array()
        assert runtime.store.files[f'raw/network/{row["id"]}/request.body'] == b"\xff\x00\x80"
    assert {runtime.store.files[f'raw/network/{row["id"]}/response.body'] for row in records} == {
        b"second\xff", b"first\x80"}
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_request_metadata_is_durable_without_response(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    request = FakeRequest()
    runtime._on_request(request)
    runtime._on_request_failed(request)
    await runtime.drain()
    assert metadata(runtime)[0]["failure"] == "NS_ERROR_CONNECTION_REFUSED"
    assert metadata(runtime)[0]["request_headers"][0]["name"] == "Cookie"
    assert (await runtime.close())["status"] == "closed"


@pytest.mark.asyncio
async def test_context_events_capture_popup_and_worker_without_frame(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    for _ in range(2):
        request = FakeRequest()
        runtime.context.handlers["request"](request)
        runtime.context.handlers["response"](FakeResponse(request))
    await runtime.drain()
    assert len(metadata(runtime)) == 2
    result = await runtime.close()
    assert "worker" in json.dumps(result).lower()


@pytest.mark.asyncio
async def test_snapshot_twice_preserves_state_and_per_frame_session_storage(runtime_factory):
    runtime = await runtime_factory().__aenter__()

    class Frame:
        url = "https://fixture.test/frame"

        async def evaluate(self, script):
            return {"key": "first"}

    class InaccessibleFrame(Frame):
        async def evaluate(self, script):
            raise RuntimeError("frame detached")

    runtime.page.frames = [Frame(), InaccessibleFrame()]
    first = await runtime.snapshot()
    original = Path(first["path"]).read_bytes()
    second = await runtime.snapshot()
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == original
    state = json.loads(original)
    assert state["cookies"][0]["value"] == "fixture"
    assert state["sessionStorage"][0]["storage"] == {"key": "first"}
    assert state["sessionStorage"][1]["status"] == "inaccessible"
    assert (await runtime.close())["status"] == "incomplete"


@pytest.mark.asyncio
async def test_drain_waits_for_tasks_spawned_by_capture(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    completed = []

    async def child():
        await asyncio.sleep(0.01)
        completed.append(True)

    async def parent():
        runtime._spawn(child())

    runtime._spawn(parent())
    await runtime.drain()
    assert completed == [True]
    await runtime.close()


@pytest.mark.asyncio
async def test_drain_timeout_records_gap_and_cancels_work(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    runtime._spawn(asyncio.Event().wait())
    await runtime.drain(timeout=0.01)
    result = await runtime.close()
    assert result["status"] == "incomplete"
    assert "timeout" in json.dumps(result).lower()
    assert not runtime._request_tasks


@pytest.mark.asyncio
async def test_cleanup_runs_even_when_drain_raises(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()

    async def fail():
        raise RuntimeError("drain broken")

    monkeypatch.setattr(runtime, "drain", fail)
    result = await runtime.close()
    assert runtime._cm.exited
    assert result["status"] == "incomplete"


@pytest.mark.asyncio
async def test_index_failure_uses_emergency_session_status(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()

    def fail(*args, **kwargs):
        raise RuntimeError("index unavailable")

    def emergency(status):
        runtime.session.manifest_path.write_text(json.dumps({"status": status}))

    monkeypatch.setattr(runtime.store, "finalize", fail)
    monkeypatch.setattr(runtime.store, "rebuild_index", fail)
    monkeypatch.setattr(runtime.session, "mark_incomplete", fail)
    monkeypatch.setattr(runtime.session, "_close_manifest", emergency, raising=False)
    result = await runtime.close()
    assert result["status"] == "incomplete"
    assert runtime.session.manifest_snapshot()["status"] == "incomplete"
    assert "index unavailable" in json.dumps(result)
    assert runtime._cm.exited


@pytest.mark.asyncio
async def test_redirect_and_unanswered_request_are_preserved(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    first, second, unanswered = FakeRequest(), FakeRequest(), FakeRequest()
    first.redirected_to = second
    second.redirected_from = first
    for request in (first, second, unanswered):
        runtime._on_request(request)
    response = FakeResponse(first)
    response.status = 302
    runtime._on_response(response)
    runtime._on_response(FakeResponse(second))
    result = await runtime.close()
    records = metadata(runtime)
    assert len(records) == 3
    redirect = next(row for row in records if row.get("status") == 302)
    target = next(row for row in records if row.get("redirected_from") == redirect["id"])
    assert redirect["redirected_to"] == target["id"]
    assert result["status"] == "incomplete"


def native_process(root, pid, *, sidecar=True):
    control = root / "control"
    control.mkdir(parents=True, exist_ok=True)
    traces = root / "traces"
    traces.mkdir(exist_ok=True)
    command = control / f"control-{pid}.cmd"
    status = control / f"status-{pid}.state"
    command.write_text("on")
    status.write_text("on 0 events=1 dropped=0\n")
    trace = traces / f"{pid}_0.jsonl"
    trace.write_bytes(b'{"sequence":1}\n')
    if sidecar:
        Path(str(trace) + ".meta.json").write_text(
            '{"state":"off","session_id":0,"events":1,"dropped":0}\n'
        )
    return command, status, trace


@pytest.mark.asyncio
async def test_native_stop_acknowledges_new_processes_before_browser_exit(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    runtime.enable_trace = True
    root = runtime.session.trace_dir
    first, first_status, trace = native_process(root, 101)

    async def producer():
        while first.read_text().strip() != "off":
            await asyncio.sleep(0.005)
        assert not runtime._cm.exited
        assert (root / "desired.state").read_text().strip() == "off"
        first_status.write_text("off 0 events=1 dropped=0\n")
        await asyncio.sleep(0.06)
        nested = root / "late-process"
        second, second_status, _ = native_process(nested, 102)
        while second.read_text().strip() != "off":
            await asyncio.sleep(0.005)
        assert not runtime._cm.exited
        assert (nested / "desired.state").read_text().strip() == "off"
        second_status.write_text("off 0 events=1 dropped=0\n")

    task = asyncio.create_task(producer())
    try:
        result = await asyncio.wait_for(runtime.close(), timeout=4)
        await asyncio.wait_for(task, timeout=0.5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert result["status"] == "closed"
    captured = [data for path, data in runtime.store.files.items()
                if path.startswith("raw/native-status/") and path.endswith(".state")]
    assert len(captured) >= 2
    assert all(data.startswith(b"off ") for data in captured)
    assert trace.read_bytes() == b'{"sequence":1}\n'
    assert runtime._cm.exited


@pytest.mark.asyncio
async def test_native_stop_missing_ack_and_sidecar_are_gaps(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    runtime.enable_trace = True
    command, status, trace = native_process(runtime.session.trace_dir, 201, sidecar=False)
    await runtime._stop_native_trace(timeout=0.03)
    runtime.enable_trace = False
    result = await runtime.close()
    assert result["status"] == "incomplete"
    errors = json.dumps(result["capture"]["errors"])
    assert "native_stop_ack" in errors
    assert "native_sidecar" in errors
    assert command.read_text().strip() == "off"
    assert status.read_text().startswith("on ")
    assert trace.exists()


@pytest.mark.asyncio
async def test_native_stop_empty_control_is_unknown_not_zero(runtime_factory):
    runtime = await runtime_factory().__aenter__()
    runtime.enable_trace = True
    await runtime._stop_native_trace(timeout=0.01)
    runtime.enable_trace = False
    result = await runtime.close()
    assert result["status"] == "incomplete"
    assert "native_producers" in json.dumps(result)


@pytest.mark.asyncio
async def test_native_stop_does_not_follow_session_trace_symlinks(runtime_factory, tmp_path):
    runtime = await runtime_factory().__aenter__()
    runtime.enable_trace = True
    outside = tmp_path / "outside"
    outside.mkdir()
    command, _, _ = native_process(outside, 301)
    (runtime.session.trace_dir / "control").symlink_to(outside / "control")
    await runtime._stop_native_trace(timeout=0.01)
    runtime.enable_trace = False
    result = await runtime.close()
    assert command.read_text() == "on"
    assert result["status"] == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,status,headers,availability", [
    ("HEAD", 200, [], "empty_by_http_semantics"),
    ("GET", 204, [], "empty_by_http_semantics"),
    ("GET", 304, [], "empty_by_http_semantics"),
    ("GET", 302, [{"name": "Content-Length", "value": "0"}], "empty_by_header"),
])
async def test_empty_response_semantics_skip_unavailable_body(
    runtime_factory, method, status, headers, availability
):
    runtime = await runtime_factory().__aenter__()
    request = FakeRequest()
    request.method = method

    class Response(FakeResponse):
        async def body(self):
            pytest.fail("semantically empty responses must not request a body")

        async def headers_array(self):
            return headers

    response = Response(request)
    response.status = status
    runtime._on_request(request)
    runtime._on_response(response)
    result = await runtime.close()
    row = metadata(runtime)[0]
    assert row["body_availability"] == availability
    assert runtime.store.files[f'raw/network/{row["id"]}/response.body'] == b""
    assert result["status"] == "closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [[], [{"name": "Content-Length", "value": "25"}]])
async def test_redirect_unknown_or_nonzero_body_is_not_invented(runtime_factory, headers):
    runtime = await runtime_factory().__aenter__()
    request = FakeRequest()

    class Response(FakeResponse):
        status = 302

        async def body(self):
            raise RuntimeError("unavailable redirect body")

        async def headers_array(self):
            return headers

    runtime._on_request(request)
    runtime._on_response(Response(request))
    result = await runtime.close()
    row = metadata(runtime)[0]
    assert row["body_availability"] == "unavailable"
    assert f'raw/network/{row["id"]}/response.body' not in runtime.store.files
    assert result["status"] == "incomplete"


@pytest.mark.asyncio
async def test_cancelled_shutdown_drain_still_persists_incomplete(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()
    shutdown_started = asyncio.Event()
    original = runtime.drain

    async def drain():
        if runtime._cm.exited:
            shutdown_started.set()
            await asyncio.Event().wait()
        await original()

    monkeypatch.setattr(runtime, "drain", drain)
    closing = asyncio.create_task(runtime.close())
    await shutdown_started.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert runtime.session.manifest_snapshot()["status"] == "incomplete"
    assert runtime._cm.exited


@pytest.mark.asyncio
async def test_native_stop_failure_never_skips_browser_cleanup(runtime_factory, monkeypatch):
    runtime = await runtime_factory().__aenter__()

    async def fail():
        raise RuntimeError("native control inaccessible")

    monkeypatch.setattr(runtime, "_stop_native_trace", fail)
    result = await runtime.close()
    assert runtime._cm.exited
    assert result["status"] == "incomplete"
    assert "native control inaccessible" in json.dumps(result)
