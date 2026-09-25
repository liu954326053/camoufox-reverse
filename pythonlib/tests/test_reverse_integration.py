"""Browser-free integration coverage for the reverse-analysis foundation."""

import hashlib
import json
from pathlib import Path

import pytest

from camoufox import reverse_launch_options
from camoufox.reverse_compat import BROWSER_SELECTOR
from camoufox.reverse_evidence import EvidenceError, EvidenceStore
from camoufox.reverse_project import ReverseProject


def test_project_launch_and_incomplete_session_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "camoufox.reverse_launch.launch_options",
        lambda **kwargs: kwargs,
    )
    project_dir = tmp_path / "analysis"
    project = ReverseProject.open(project_dir)

    first_session = project.create_session()
    options, second_session = reverse_launch_options(
        project_dir=project_dir,
        trace_profile="targeted",
        browser_version=BROWSER_SELECTOR,
    )

    assert second_session.session_id != first_session.session_id
    assert options["browser"] == BROWSER_SELECTOR
    assert options["config"]["propertyTrace"]["logDir"] == str(
        second_session.trace_dir
    )

    first_session.mark_incomplete(RuntimeError("fixture crash"))
    sessions = {item["session_id"]: item for item in project.list_sessions()}

    assert sessions[first_session.session_id]["status"] == "incomplete"
    assert sessions[second_session.session_id]["status"] == "running"

    resumed = project.create_session(resume_session=first_session.session_id)
    assert resumed.session_id == first_session.session_id
    assert resumed.manifest_snapshot()["status"] == "starting"


def test_raw_values_survive_index_rebuild(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    session = project.create_session()
    store = EvidenceStore(session)
    raw_value = b"token=raw-secret\x00\xff\n"

    raw_path = session.path / "raw" / "network" / "response.bin"
    store.append_bytes("raw/network/response.bin", raw_value)
    store.append_jsonl(
        "raw/network/events.jsonl",
        {"body": "raw-value", "unicode": "雪", "sequence": 7},
    )
    store.register_artifact(
        "raw/network/response.bin",
        hashlib.sha256(raw_value).hexdigest(),
        len(raw_value),
        "response",
    )

    before = raw_path.read_bytes()
    index_path = store.rebuild_index()
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert raw_path.read_bytes() == before == raw_value
    assert index["files"]["raw/network/response.bin"]["bytes"] == len(raw_value)
    assert index["files"]["raw/network/events.jsonl"]["event_count"] == 1
    assert index["artifacts"][0]["path"] == "raw/network/events.jsonl"


def test_integration_artifacts_stay_inside_project_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "camoufox.reverse_launch.launch_options",
        lambda **kwargs: kwargs,
    )
    project = ReverseProject.open(tmp_path / "analysis")
    options, session = reverse_launch_options(project_dir=project.path)
    store = EvidenceStore(session)

    with pytest.raises(EvidenceError):
        store.append_bytes("../outside.bin", b"must stay inside")

    index_path = store.rebuild_index()
    project_root = project.path.resolve()
    paths = [
        session.path,
        session.manifest_path,
        session.trace_dir,
        index_path,
        Path(options["config"]["propertyTrace"]["logDir"]),
    ]

    assert not (project.path.parent / "outside.bin").exists()
    assert all(path.resolve().is_relative_to(project_root) for path in paths)
    assert all(path.resolve().is_relative_to(project_root) for path in project.path.rglob("*"))
