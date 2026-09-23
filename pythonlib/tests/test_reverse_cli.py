"""CLI contract tests for the project-scoped reverse browser entrypoint."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from camoufox.reverse_cli import cli
from camoufox.reverse_evidence import EvidenceStore
from camoufox.reverse_project import ReverseProject, ReverseSession


@pytest.fixture
def runner():
    return CliRunner()


class FakePage:
    def __init__(self):
        self.urls = []

    async def goto(self, url):
        self.urls.append(url)


class FakeRuntime:
    instances = []
    fail_enter = False

    def __init__(self, *, project_dir, **kwargs):
        self.project_dir = Path(project_dir)
        self.kwargs = kwargs
        self.session = None
        self.page = FakePage()
        self.entered = False
        self.closed = False
        self.drained = False
        type(self).instances.append(self)

    async def __aenter__(self):
        if type(self).fail_enter:
            raise RuntimeError("browser launch failed")
        self.session = ReverseProject.open(self.project_dir).create_session()
        self.entered = True
        return self

    async def drain(self):
        self.drained = True

    async def close(self, incomplete=False):
        self.closed = True
        if self.session is not None:
            self.session.close("incomplete" if incomplete else "complete")
        return {
            "status": "incomplete" if incomplete else "complete",
            "session_id": self.session.session_id,
            "session_dir": str(self.session.path),
            "artifacts": {},
        }

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close(incomplete=exc_type is not None)


@pytest.fixture(autouse=True)
def reset_fake_runtime(monkeypatch):
    FakeRuntime.instances = []
    FakeRuntime.fail_enter = False
    monkeypatch.setattr("camoufox.reverse_cli._runtime_factory", lambda: FakeRuntime)
    monkeypatch.setattr(
        ReverseSession,
        "manifest_snapshot",
        lambda session: json.loads(session.manifest_path.read_text()),
        raising=False,
    )


def test_launch_requires_project_dir_as_json_error(runner):
    result = runner.invoke(cli, ["launch"])

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload == {
        "error": {
            "code": "missing_option",
            "message": "Missing option '--project-dir'.",
        },
        "status": "error",
    }


def test_invalid_choice_is_json_error(runner, tmp_path):
    result = runner.invoke(
        cli,
        [
            "launch",
            "--project-dir",
            str(tmp_path / "p"),
            "--trace-profile",
            "invalid",
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_choice"


def test_session_list_returns_json(runner, tmp_path):
    result = runner.invoke(
        cli, ["session", "list", "--project-dir", str(tmp_path / "p")]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["sessions"] == []


def test_launch_holds_fake_runtime_and_emits_after_browser_enter(
    runner, tmp_path
):
    result = runner.invoke(
        cli,
        [
            "launch",
            "--project-dir",
            str(tmp_path / "p"),
            "--proxy",
            "http://alice:secret@proxy:8080",
            "--browser-version",
            "official/beta.20",
            "--trace-profile",
            "targeted",
            "--headless",
            "--no-trace",
            "--duration",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "launched"
    assert payload["session_id"]
    assert payload["artifacts"]["manifest"].endswith("manifest.json")
    assert "secret" not in result.output
    assert "proxy" not in payload
    runtime = FakeRuntime.instances[0]
    assert runtime.entered
    assert runtime.closed
    assert runtime.drained
    assert runtime.kwargs == {
        "proxy": "http://alice:secret@proxy:8080",
        "browser_version": "official/beta.20",
        "trace_profile": "targeted",
        "enable_trace": False,
        "headless": True,
    }


def test_launch_navigates_url_before_duration_exit(runner, tmp_path):
    result = runner.invoke(
        cli,
        [
            "launch",
            "--project-dir",
            str(tmp_path / "p"),
            "--url",
            "https://example.test/",
            "--duration",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    runtime = FakeRuntime.instances[0]
    assert runtime.page.urls == ["https://example.test/"]


def test_launch_browser_enter_failure_is_json_error_without_launched(
    runner, tmp_path
):
    FakeRuntime.fail_enter = True

    result = runner.invoke(
        cli,
        ["launch", "--project-dir", str(tmp_path / "p"), "--duration", "0"],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "runtime_error"
    assert "launched" not in result.output


def test_trace_index_rebuilds_session_index(runner, tmp_path):
    session = ReverseProject.open(tmp_path / "p").create_session()
    EvidenceStore(session).append_jsonl("raw/events.jsonl", {"sequence": 1})

    result = runner.invoke(
        cli,
        [
            "trace",
            "index",
            "--project-dir",
            str(tmp_path / "p"),
            "--session",
            session.session_id,
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["session_id"] == session.session_id
    assert payload["artifacts"]["index"].endswith(f"{session.session_id}.json")


def test_report_build_writes_report_for_indexed_session(runner, tmp_path):
    session = ReverseProject.open(tmp_path / "p").create_session()
    EvidenceStore(session).append_jsonl("raw/events.jsonl", {"sequence": 1})

    result = runner.invoke(
        cli,
        [
            "report",
            "build",
            "--project-dir",
            str(tmp_path / "p"),
            "--session",
            session.session_id,
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report_path = tmp_path / "p" / "runs" / session.session_id / "report" / "report.json"
    assert payload["status"] == "ok"
    assert payload["artifacts"]["report"] == str(report_path)
    report = json.loads(report_path.read_text())
    assert report["session_id"] == session.session_id
    assert report["index"]


def test_report_rejects_index_with_wrong_session_id(runner, tmp_path):
    project = ReverseProject.open(tmp_path / "p")
    session = project.create_session()
    EvidenceStore(session).append_jsonl("raw/events.jsonl", {"sequence": 1})
    index_path = EvidenceStore(session).rebuild_index()
    index = json.loads(index_path.read_text())
    index["session_id"] = "0" * 32
    index_path.write_text(json.dumps(index))

    result = runner.invoke(
        cli,
        [
            "report",
            "build",
            "--project-dir",
            str(project.path),
            "--session",
            session.session_id,
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "invalid_index"


def test_report_rejects_index_symlink(runner, tmp_path):
    project = ReverseProject.open(tmp_path / "p")
    session = project.create_session()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema": 1, "session_id": session.session_id}))
    index_path = project.indexes_dir / f"{session.session_id}.json"
    index_path.symlink_to(outside)

    result = runner.invoke(
        cli,
        [
            "report",
            "build",
            "--project-dir",
            str(project.path),
            "--session",
            session.session_id,
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "invalid_index"


def test_report_rejects_out_of_project_artifact_path(runner, tmp_path):
    project = ReverseProject.open(tmp_path / "p")
    session = project.create_session()
    index_path = project.indexes_dir / f"{session.session_id}.json"
    index_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "session_id": session.session_id,
                "event_loss": 0,
                "artifacts": [{"path": "../outside.json"}],
                "files": {},
            }
        )
    )

    result = runner.invoke(
        cli,
        [
            "report",
            "build",
            "--project-dir",
            str(project.path),
            "--session",
            session.session_id,
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "invalid_index"


def test_invalid_session_returns_nonzero_json_error(runner, tmp_path):
    result = runner.invoke(
        cli,
        [
            "trace",
            "index",
            "--project-dir",
            str(tmp_path / "p"),
            "--session",
            "0" * 32,
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert "session" in payload["error"]["message"].lower()
