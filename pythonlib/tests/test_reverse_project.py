"""Tests for reverse-analysis project and session lifecycle boundaries."""

import json
import os
import shutil

import pytest

from camoufox import reverse_project
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


@pytest.mark.parametrize("directory_name", ["runs", "indexes"])
def test_open_rejects_project_directory_symlinks(tmp_path, directory_name):
    project_path = tmp_path / "analysis"
    project_path.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_path / directory_name).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectError):
        ReverseProject.open(project_path)


def test_each_launch_gets_a_unique_isolated_session(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")

    first = project.create_session()
    second = project.create_session()

    assert first.session_id != second.session_id
    assert first.path != second.path
    assert first.raw_dir != second.raw_dir


def test_session_exposes_trace_dir_as_a_stable_public_path(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()

    assert session.trace_dir == session.path / "trace"


def test_mark_running_updates_manifest_through_public_lifecycle_api(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()

    session.mark_running(
        trace_profile="targeted",
        browser_version="official/beta.20",
        proxy={"server": "http://127.0.0.1:7890", "authenticated": False},
    )

    manifest = json.loads(session.manifest_path.read_text())
    assert manifest["status"] == "running"
    assert manifest["trace_profile"] == "targeted"
    assert manifest["browser_version"] == "official/beta.20"
    assert manifest["proxy"]["server"] == "http://127.0.0.1:7890"


def test_mark_running_rejects_a_completed_session(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()
    session.close()

    with pytest.raises(ProjectError):
        session.mark_running()


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


@pytest.mark.parametrize("session_id", [".", "..", "not/a/session", "not-a-uuid"])
def test_resume_rejects_unsafe_session_ids(tmp_path, session_id):
    project = ReverseProject.open(tmp_path / "analysis")

    with pytest.raises(ProjectError):
        project.create_session(resume_session=session_id)


def test_resume_rejects_session_symlink(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "raw").mkdir()
    (outside / "manifest.json").write_text(
        json.dumps({"schema": 1, "session_id": "0" * 32, "status": "incomplete"})
    )
    (project.runs_dir / ("0" * 32)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectError):
        project.create_session(resume_session="0" * 32)


def test_resume_rejects_raw_symlink(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    session = project.create_session()
    session.mark_incomplete()
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(session.raw_dir)
    session.raw_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectError):
        project.create_session(resume_session=session.session_id)


@pytest.mark.parametrize(
    "manifest_change",
    [
        {"schema": 2},
        {"session_id": "0" * 32},
    ],
)
def test_resume_rejects_invalid_manifest_identity(tmp_path, manifest_change):
    project = ReverseProject.open(tmp_path / "analysis")
    session = project.create_session()
    session.mark_incomplete()
    manifest = json.loads(session.manifest_path.read_text())
    manifest.update(manifest_change)
    session.manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ProjectError):
        project.create_session(resume_session=session.session_id)


def test_resume_rejects_manifest_with_non_directory_raw(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    session = project.create_session()
    session.mark_incomplete()
    shutil.rmtree(session.raw_dir)
    session.raw_dir.write_text("not a directory")

    with pytest.raises(ProjectError):
        project.create_session(resume_session=session.session_id)


@pytest.mark.parametrize("method", ["close", "mark_incomplete"])
def test_stale_resumed_handle_cannot_overwrite_completed_session(tmp_path, method):
    project = ReverseProject.open(tmp_path / "analysis")
    original = project.create_session()
    original.mark_incomplete()
    resumed = project.create_session(resume_session=original.session_id)

    original.close()
    with pytest.raises(ProjectError):
        getattr(resumed, method)()

    assert json.loads(original.manifest_path.read_text())["status"] == "complete"


def test_failed_session_initialization_removes_partial_directory(tmp_path, monkeypatch):
    project = ReverseProject.open(tmp_path / "analysis")

    def fail_manifest(*args, **kwargs):
        raise ProjectError("manifest failure")

    monkeypatch.setattr(reverse_project, "_write_manifest", fail_manifest)

    with pytest.raises(ProjectError):
        project.create_session()

    assert list(project.runs_dir.iterdir()) == []


def test_failed_raw_directory_initialization_removes_partial_directory(tmp_path, monkeypatch):
    project = ReverseProject.open(tmp_path / "analysis")
    original_mkdir = reverse_project.Path.mkdir

    def fail_raw_directory(path, *args, **kwargs):
        if path.name == "raw":
            raise OSError("raw directory failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(reverse_project.Path, "mkdir", fail_raw_directory)

    with pytest.raises(ProjectError):
        project.create_session()

    assert list(project.runs_dir.iterdir()) == []


def test_manifest_replace_attempts_to_fsync_parent_directory(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(
        reverse_project,
        "_fsync_directory",
        lambda path: synced.append(path),
    )

    session = ReverseProject.open(tmp_path / "analysis").create_session()

    assert synced == [session.path]


def test_closed_session_is_immutable(tmp_path):
    session = ReverseProject.open(tmp_path / "analysis").create_session()
    session.close()
    before = session.manifest_path.read_text()

    with pytest.raises(ProjectError):
        session.mark_incomplete(RuntimeError("late failure"))

    assert session.manifest_path.read_text() == before
