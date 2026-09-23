"""Project-scoped launch configuration for reverse-analysis sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from .reverse_project import ReverseProject, ReverseSession, _write_manifest
from .utils import launch_options


_TRACE_PROFILES: dict[str, dict[str, Any]] = {
    "overview": {
        "objects": ["navigator", "screen", "window"],
        "maxEventsPerSession": 10_000,
    },
    "targeted": {
        "objects": [
            "navigator",
            "screen",
            "window",
            "document",
            "location",
            "performance",
            "crypto",
        ],
        "maxEventsPerSession": 100_000,
    },
    "deep": {
        "objects": [],
        "maxEventsPerSession": 500_000,
    },
}


def _proxy_dict(proxy_url: str) -> dict[str, str]:
    if not isinstance(proxy_url, str) or not proxy_url.strip():
        raise ValueError("proxy must be a non-empty URL")

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("proxy must include a scheme and host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("proxy port is invalid") from exc

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    server = f"{parsed.scheme}://{host}"
    if port is not None:
        server += f":{port}"

    result = {"server": server}
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


def _trace_dir(session: ReverseSession) -> Path:
    trace_dir = session.path / "trace"
    if trace_dir.is_symlink():
        raise ValueError("session trace directory cannot be a symlink")
    trace_dir.mkdir(mode=0o700, exist_ok=True)
    if not trace_dir.is_dir() or trace_dir.resolve(strict=True).parent != session.path.resolve(
        strict=True
    ):
        raise ValueError("session trace directory is invalid")
    try:
        trace_dir.chmod(0o700)
    except OSError as exc:
        raise ValueError("session trace directory is not secure") from exc
    # Task 1's session object predates the trace directory accessor. Keep the
    # public interface promised by the reverse launch contract without
    # changing the already-stable project/session module.
    session.trace_dir = trace_dir  # type: ignore[attr-defined]
    return trace_dir


def _mark_running(
    session: ReverseSession,
    *,
    trace_profile: str,
    proxy: dict[str, str] | None,
    browser_version: str | None,
) -> None:
    manifest = dict(session._manifest)
    manifest.update(
        {
            "status": "running",
            "trace_profile": trace_profile,
            "browser_version": browser_version,
            "proxy": (
                {
                    "server": proxy["server"],
                    "authenticated": "username" in proxy or "password" in proxy,
                }
                if proxy
                else None
            ),
        }
    )
    _write_manifest(session.manifest_path, manifest)
    session._manifest = manifest


def reverse_launch_options(
    project_dir: str | Path,
    proxy: str | None = None,
    browser_version: str | None = None,
    trace_profile: str = "overview",
    resume_session: str | None = None,
    **kwargs: Any,
) -> tuple[dict[str, Any], ReverseSession]:
    """Build Camoufox launch options tied to one reverse-analysis session.

    The caller owns the browser and must close the returned session after the
    browser exits. Proxy credentials are passed to Playwright only through its
    structured proxy fields and are never included in the session manifest.
    """
    try:
        profile = _TRACE_PROFILES[trace_profile]
    except KeyError as exc:
        raise ValueError(
            "trace_profile must be one of: overview, targeted, deep"
        ) from exc

    project = ReverseProject.open(project_dir)
    session = project.create_session(resume_session=resume_session)
    trace_dir = _trace_dir(session)

    config = dict(kwargs.pop("config", None) or {})
    caller_trace = config.get("propertyTrace", {})
    if caller_trace is None:
        caller_trace = {}
    if not isinstance(caller_trace, Mapping):
        raise TypeError("config.propertyTrace must be a mapping")
    trace_config = {
        **profile,
        **dict(caller_trace),
        "enabled": True,
        "logDir": str(trace_dir),
    }
    config["propertyTrace"] = trace_config

    launch_kwargs = dict(kwargs)
    launch_kwargs["config"] = config
    normalized_proxy = _proxy_dict(proxy) if proxy is not None else launch_kwargs.get("proxy")
    if proxy is not None:
        launch_kwargs["proxy"] = normalized_proxy
    if browser_version is not None:
        launch_kwargs["browser"] = browser_version

    options = launch_options(**launch_kwargs)
    _mark_running(
        session,
        trace_profile=trace_profile,
        proxy=normalized_proxy,
        browser_version=browser_version or launch_kwargs.get("browser"),
    )
    return options, session
