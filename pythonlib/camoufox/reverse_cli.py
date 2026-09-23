"""Thin JSON CLI for project-scoped reverse browser sessions."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

import rich_click as click

from .reverse_evidence import EvidenceError, EvidenceStore
from .reverse_launch import reverse_launch_options
from .reverse_project import ProjectError, ReverseProject, ReverseSession


def _json_output(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _error_output(error: BaseException) -> None:
    known_error = isinstance(error, (ProjectError, EvidenceError, OSError, ValueError, TypeError))
    message = str(error) if known_error else "reverse-browser operation failed"
    _json_output({"status": "error", "error": {"message": message}})
    raise click.exceptions.Exit(1)


def _run(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as error:
        _error_output(error)


def _session_artifacts(session: ReverseSession) -> dict[str, str]:
    return {
        "manifest": str(session.manifest_path),
        "session_dir": str(session.path),
        "trace_dir": str(session.trace_dir),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor != -1:
                os.close(descriptor)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_index(session: ReverseSession) -> tuple[Path, dict[str, Any]]:
    index_path = session.project.indexes_dir / f"{session.session_id}.json"
    if not index_path.exists():
        index_path = EvidenceStore(session).rebuild_index()
    return index_path, json.loads(index_path.read_text(encoding="utf-8"))


@click.group()
def cli() -> None:
    """Inspect and launch project-scoped reverse browser sessions."""


@cli.command("launch")
@click.option("--project-dir", type=click.Path(path_type=Path), required=True)
@click.option("--proxy", default=None)
@click.option("--browser-version", default=None)
@click.option("--trace-profile", type=click.Choice(["overview", "targeted", "deep"]), default="overview")
def launch(project_dir: Path, proxy: str | None, browser_version: str | None, trace_profile: str) -> None:
    """Create a reverse session and return its launch metadata."""

    def action() -> None:
        _, session = reverse_launch_options(
            project_dir=project_dir,
            proxy=proxy,
            browser_version=browser_version,
            trace_profile=trace_profile,
        )
        _json_output(
            {
                "status": "launched",
                "session_id": session.session_id,
                "artifacts": _session_artifacts(session),
            }
        )

    _run(action)


@cli.group("session")
def session_group() -> None:
    """Manage reverse browser sessions."""


@session_group.command("list")
@click.option("--project-dir", type=click.Path(path_type=Path), required=True)
def session_list(project_dir: Path) -> None:
    """List session lifecycle metadata as JSON."""
    _run(lambda: _json_output({"sessions": ReverseProject.open(project_dir).list_sessions()}))


@cli.group("trace")
def trace_group() -> None:
    """Build and inspect trace indexes."""


@trace_group.command("index")
@click.option("--project-dir", type=click.Path(path_type=Path), required=True)
@click.option("--session", "session_id", required=True)
def trace_index(project_dir: Path, session_id: str) -> None:
    """Rebuild the deterministic index for one incomplete session."""

    def action() -> None:
        project = ReverseProject.open(project_dir)
        session = project.get_session(session_id)
        index_path = EvidenceStore(session).rebuild_index()
        _json_output(
            {
                "status": "ok",
                "session_id": session.session_id,
                "artifacts": {"index": str(index_path)},
            }
        )

    _run(action)


@cli.group("report")
def report_group() -> None:
    """Build derived reports without modifying raw evidence."""


@report_group.command("build")
@click.option("--project-dir", type=click.Path(path_type=Path), required=True)
@click.option("--session", "session_id", required=True)
def report_build(project_dir: Path, session_id: str) -> None:
    """Build a JSON report from one session's deterministic index."""

    def action() -> None:
        project = ReverseProject.open(project_dir)
        session = project.get_session(session_id)
        index_path, index = _load_index(session)
        report_path = session.path / "report" / "report.json"
        report = {
            "schema": 1,
            "session_id": session.session_id,
            "status": session._manifest["status"],
            "event_loss": index.get("event_loss", 0),
            "index": str(index_path),
            "files": index.get("files", {}),
            "artifacts": index.get("artifacts", []),
        }
        _write_json(report_path, report)
        _json_output(
            {
                "status": "ok",
                "session_id": session.session_id,
                "artifacts": {"report": str(report_path), "index": str(index_path)},
            }
        )

    _run(action)


if __name__ == "__main__":
    cli()
