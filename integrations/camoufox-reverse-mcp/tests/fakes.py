from __future__ import annotations

import json
from pathlib import Path


class FakeSession:
    session_id = "session-tools"

    def __init__(self, root: Path):
        self.path = root
        self.path.mkdir(parents=True, exist_ok=True)
        self.trace_dir = root / "trace"
        self.trace_dir.mkdir(exist_ok=True)
        self.manifest_path = root / "manifest.json"
        self.manifest_path.write_text(
            json.dumps({"status": "running", "session_id": self.session_id}),
            encoding="utf-8",
        )

    def manifest_snapshot(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))


class FakePage:
    url = "about:blank"

    def on(self, *_args):
        return None


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]


class FakeBrowser:
    def __init__(self):
        self.contexts = [FakeContext()]


class FakeRuntime:
    def __init__(self, project_dir, **kwargs):
        self.project_dir = Path(project_dir)
        self.kwargs = kwargs
        self.session = FakeSession(self.project_dir / "runs" / "session-tools")
        self.context = FakeContext()
        self.page = self.context.pages[0]
        self.browser = None
        self.store = object()
        self.files: dict[str, bytes] = {}

    async def __aenter__(self):
        self.browser = FakeBrowser()
        return self

    def write_artifact(self, relative_path, data, kind):
        path = self.session.path / relative_path
        if path.exists() or relative_path in self.files:
            raise ValueError("artifact already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[relative_path] = bytes(data)
        return path

    async def close(self, incomplete=False):
        return {
            "status": "incomplete" if incomplete else "closed",
            "session_id": self.session.session_id,
            "session_dir": str(self.session.path.resolve()),
            "artifacts": {},
        }


def make_fake_runtime(project_dir, **kwargs):
    return FakeRuntime(project_dir, **kwargs)
