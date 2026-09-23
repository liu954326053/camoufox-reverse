"""Thin JSON CLI for project-scoped reverse browser sessions."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import uuid
from pathlib import Path
from typing import Any, Callable

import rich_click as click

from .reverse_evidence import EvidenceError, EvidenceStore
from .reverse_project import ProjectError, ReverseProject, ReverseSession


class InvalidIndexError(EvidenceError):
    """Raised when a report index is malformed or outside its project boundary."""


def _json_output(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _error_code(error: BaseException) -> str:
    if isinstance(error, click.MissingParameter):
        return "missing_option"
    if isinstance(error, click.BadParameter):
        if isinstance(getattr(getattr(error, "param", None), "type", None), click.Choice):
            return "invalid_choice"
        return "invalid_value"
    if isinstance(error, click.UsageError):
        return "usage_error"
    if isinstance(error, ProjectError):
        return "project_error"
    if isinstance(error, InvalidIndexError):
        return "invalid_index"
    if isinstance(error, EvidenceError):
        return "evidence_error"
    if isinstance(error, (ValueError, TypeError)):
        return "invalid_value"
    if isinstance(error, OSError):
        return "io_error"
    return "runtime_error"


def _click_error_message(error: click.ClickException) -> str:
    if isinstance(error, click.MissingParameter):
        parameter = getattr(error, "param", None)
        hint = getattr(error, "param_hint", None) or getattr(parameter, "name", None) or "parameter"
        if not hint.startswith("-"):
            hint = f"--{hint.replace('_', '-')}"
        return f"Missing option '{hint}'."
    return str(error)


def _error_output(error: BaseException) -> None:
    _json_output(
        {
            "status": "error",
            "error": {
                "code": _error_code(error),
                "message": str(error) or "reverse-browser operation failed",
            },
        }
    )
    raise click.exceptions.Exit(1)


def _run(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as error:
        _error_output(error)


class _JsonGroup(click.Group):
    """Render Click parser failures with the regular JSON error contract."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        standalone_mode = kwargs.get("standalone_mode", True)
        kwargs["standalone_mode"] = False
        try:
            result = super().main(*args, **kwargs)
            if standalone_mode and isinstance(result, int) and result:
                raise click.exceptions.Exit(result)
            return result
        except click.ClickException as error:
            _json_output(
                {
                    "status": "error",
                    "error": {
                        "code": _error_code(error),
                        "message": _click_error_message(error),
                    },
                }
            )
            if standalone_mode:
                raise click.exceptions.Exit(error.exit_code)
            raise


def _session_artifacts(session: ReverseSession) -> dict[str, str]:
    return {
        "manifest": str(session.manifest_path),
        "session_dir": str(session.path),
        "trace_dir": str(session.trace_dir),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    if path.is_symlink():
        raise EvidenceError(f"Report path cannot be a symlink: {path}")
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


def _runtime_factory() -> Any:
    from .reverse_runtime import AsyncReverseBrowser

    return AsyncReverseBrowser


async def _wait_for_exit() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopped.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)
    try:
        await stopped.wait()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)


async def _run_foreground(
    *,
    project_dir: Path,
    proxy: str | None,
    browser_version: str | None,
    trace_profile: str,
    url: str | None,
    duration: float | None,
    headless: bool,
    no_trace: bool,
) -> None:
    runtime = _runtime_factory()(
        project_dir=project_dir,
        proxy=proxy,
        browser_version=browser_version,
        trace_profile=trace_profile,
        enable_trace=not no_trace,
        headless=headless,
    )
    async with runtime as active:
        session = active.session
        _json_output(
            {
                "status": "launched",
                "session_id": session.session_id,
                "artifacts": _session_artifacts(session),
            }
        )
        if url is not None:
            await active.page.goto(url)
        if duration is None:
            await _wait_for_exit()
        else:
            await asyncio.sleep(duration)
        await active.drain()


def _index_path(session: ReverseSession) -> Path:
    index_path = session.project.indexes_dir / f"{session.session_id}.json"
    if index_path.is_symlink():
        raise InvalidIndexError("Session index cannot be a symlink")
    if not index_path.exists():
        index_path = EvidenceStore(session).rebuild_index()
    if index_path.is_symlink():
        raise InvalidIndexError("Session index cannot be a symlink")
    try:
        resolved = index_path.resolve(strict=True)
    except OSError as exc:
        raise InvalidIndexError("Session index cannot be resolved") from exc
    indexes_dir = session.project.indexes_dir.resolve(strict=True)
    if resolved.parent != indexes_dir or not stat.S_ISREG(resolved.stat().st_mode):
        raise InvalidIndexError("Session index must be a regular file inside the project")
    return resolved


def _validate_session_artifact_path(session: ReverseSession, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidIndexError("Index artifact path is invalid")
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise InvalidIndexError("Index artifact path must stay inside the session")
    current = session.path
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise InvalidIndexError("Index artifact path cannot contain symlinks")
    root = session.path.resolve(strict=True)
    resolved = (session.path / candidate).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise InvalidIndexError("Index artifact path is outside the session")
    return candidate.as_posix()


def _validate_index(session: ReverseSession, index: Any) -> dict[str, Any]:
    if not isinstance(index, dict):
        raise InvalidIndexError("Session index schema is invalid")
    if type(index.get("schema")) is not int or index["schema"] != 1:
        raise InvalidIndexError("Session index schema is invalid")
    if index.get("session_id") != session.session_id:
        raise InvalidIndexError("Session index identity is invalid")
    if (
        type(index.get("event_loss")) is not int
        or index["event_loss"] < 0
        or not isinstance(index.get("artifacts"), list)
        or not isinstance(index.get("files"), dict)
    ):
        raise InvalidIndexError("Session index schema is invalid")
    for path, metadata in index["files"].items():
        _validate_session_artifact_path(session, path)
        if not isinstance(metadata, dict):
            raise InvalidIndexError("Session index file metadata is invalid")
    for artifact in index["artifacts"]:
        if not isinstance(artifact, dict):
            raise InvalidIndexError("Session index artifact metadata is invalid")
        _validate_session_artifact_path(session, artifact.get("path"))
    return index


def _load_index(session: ReverseSession) -> tuple[Path, dict[str, Any]]:
    index_path = _index_path(session)
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidIndexError("Cannot read session index") from exc
    return index_path, _validate_index(session, index)


def _report_path(session: ReverseSession) -> Path:
    report_dir = session.path / "report"
    if report_dir.is_symlink():
        raise EvidenceError("Report directory cannot be a symlink")
    if report_dir.exists():
        if not report_dir.is_dir() or report_dir.resolve(strict=True).parent != session.path.resolve(
            strict=True
        ):
            raise EvidenceError("Report directory must stay inside the session")
    else:
        report_dir.mkdir(mode=0o700)
    report_path = report_dir / "report.json"
    if report_path.is_symlink():
        raise EvidenceError("Report path cannot be a symlink")
    return report_path


@click.group(cls=_JsonGroup)
def cli() -> None:
    """Inspect and launch project-scoped reverse browser sessions."""


@cli.command("launch")
@click.option("--project-dir", type=click.Path(path_type=Path), required=True)
@click.option("--proxy", default=None)
@click.option("--browser-version", default=None)
@click.option("--trace-profile", type=click.Choice(["overview", "targeted", "deep"]), default="overview")
@click.option("--url", default=None)
@click.option("--duration", type=click.FloatRange(min=0), default=None)
@click.option("--headless", is_flag=True)
@click.option("--no-trace", is_flag=True)
def launch(
    project_dir: Path,
    proxy: str | None,
    browser_version: str | None,
    trace_profile: str,
    url: str | None,
    duration: float | None,
    headless: bool,
    no_trace: bool,
) -> None:
    """Launch and hold one foreground reverse browser session."""

    def action() -> None:
        asyncio.run(
            _run_foreground(
                project_dir=project_dir,
                proxy=proxy,
                browser_version=browser_version,
                trace_profile=trace_profile,
                url=url,
                duration=duration,
                headless=headless,
                no_trace=no_trace,
            )
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
        manifest = session.manifest_snapshot()
        if not isinstance(manifest, dict) or not isinstance(manifest.get("status"), str):
            raise ProjectError("Session manifest snapshot is invalid")
        report_path = _report_path(session)
        report = {
            "schema": 1,
            "session_id": session.session_id,
            "status": manifest["status"],
            "event_loss": index["event_loss"],
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
