import asyncio
import json
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

    def on(self, name, handler):
        self.handlers[name] = handler


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]

    async def storage_state(self):
        return {"cookies": [{"name": "raw-cookie", "value": "fixture"}], "origins": []}


class FakeBrowser:
    def __init__(self):
        self.contexts = [FakeContext()]


class FakeCamoufox:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return FakeBrowser()

    async def __aexit__(self, *args):
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

    runtime = reverse_runtime.AsyncReverseBrowser(tmp_path / "project")
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
