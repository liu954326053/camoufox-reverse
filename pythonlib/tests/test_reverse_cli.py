"""CLI contract tests for the project-scoped reverse browser entrypoint."""

import json

import pytest
from click.testing import CliRunner

from camoufox.reverse_cli import cli
from camoufox.reverse_evidence import EvidenceStore
from camoufox.reverse_project import ReverseProject


@pytest.fixture
def runner():
    return CliRunner()


def test_launch_requires_project_dir(runner):
    result = runner.invoke(cli, ["launch"])

    assert result.exit_code != 0
    assert "project-dir" in result.output


def test_session_list_returns_json(runner, tmp_path):
    result = runner.invoke(
        cli, ["session", "list", "--project-dir", str(tmp_path / "p")]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["sessions"] == []


def test_launch_returns_session_metadata_without_proxy_secret(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "camoufox.reverse_cli.reverse_launch_options",
        lambda **kwargs: (
            {"proxy": {"server": "http://proxy", "password": "secret"}},
            ReverseProject.open(kwargs["project_dir"]).create_session(),
        ),
    )

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
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "launched"
    assert payload["session_id"]
    assert payload["artifacts"]["manifest"].endswith("manifest.json")
    assert "secret" not in result.output
    assert "proxy" not in payload


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
