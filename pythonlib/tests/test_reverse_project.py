"""Tests for reverse-analysis project and session lifecycle boundaries."""

import json
import os

import pytest

from camoufox.reverse_project import ProjectError, ReverseProject


def test_open_rejects_missing_project_dir_argument():
    with pytest.raises(TypeError):
        ReverseProject.open()


def test_open_creates_only_the_requested_leaf(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")

    assert project.path == (tmp_path / "analysis").resolve()
    assert (project.path / "runs").is_dir()
    assert (project.path / "indexes").is_dir()


def test_open_rejects_file_project_path(tmp_path):
    path = tmp_path / "project-file"
    path.write_text("fixture")

    with pytest.raises(ProjectError):
        ReverseProject.open(path)


def test_open_requires_existing_parent(tmp_path):
    with pytest.raises(ProjectError):
        ReverseProject.open(tmp_path / "missing" / "analysis")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions required")
def test_open_restricts_project_directories(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")

    assert project.path.stat().st_mode & 0o777 == 0o700
    assert (project.path / "runs").stat().st_mode & 0o777 == 0o700
    assert (project.path / "indexes").stat().st_mode & 0o777 == 0o700


def test_open_rejects_final_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "analysis"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ProjectError):
        ReverseProject.open(link)


def test_each_launch_gets_a_unique_isolated_session(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")

    first = project.create_session()
    second = project.create_session()

    assert first.session_id != second.session_id
    assert first.path != second.path
    assert first.raw_dir != second.raw_dir


def test_session_manifest_is_created_with_raw_capture_defaults(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()

    manifest = json.loads(session.manifest_path.read_text())

    assert manifest == {
        "schema": 1,
        "session_id": session.session_id,
        "status": "starting",
        "capture_mode": "raw",
        "contains_sensitive_data": True,
        "telemetry": False,
        "started_at": manifest["started_at"],
        "ended_at": None,
        "event_loss": 0,
        "artifacts": [],
    }


def test_close_finalizes_session_as_complete(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()

    session.close()
    manifest = json.loads(session.manifest_path.read_text())

    assert manifest["status"] == "complete"
    assert manifest["ended_at"] is not None


def test_mark_incomplete_finalizes_session_without_secret_error_fields(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()

    session.mark_incomplete(RuntimeError("secret-token"))
    manifest = json.loads(session.manifest_path.read_text())

    assert manifest["status"] == "incomplete"
    assert manifest["ended_at"] is not None
    assert "secret-token" not in session.manifest_path.read_text()


def test_resume_session_only_resumes_incomplete_session(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    session = project.create_session()
    session.mark_incomplete(RuntimeError("capture failed"))

    resumed = project.create_session(resume_session=session.session_id)

    assert resumed.session_id == session.session_id
    assert resumed.path == session.path
    assert json.loads(resumed.manifest_path.read_text())["status"] == "starting"


def test_resume_rejects_complete_session_without_overwriting_evidence(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    session = project.create_session()
    session.close()
    before = session.manifest_path.read_text()

    with pytest.raises(ProjectError):
        project.create_session(resume_session=session.session_id)

    assert session.manifest_path.read_text() == before


def test_resume_rejects_unknown_session(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")

    with pytest.raises(ProjectError):
        project.create_session(resume_session="missing-session")


def test_closed_session_is_immutable(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()
    session.close()
    before = session.manifest_path.read_text()

    with pytest.raises(ProjectError):
        session.mark_incomplete(RuntimeError("late failure"))

    assert session.manifest_path.read_text() == before
