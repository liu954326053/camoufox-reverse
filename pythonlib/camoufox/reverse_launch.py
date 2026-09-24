"""Project-scoped launch configuration for reverse-analysis sessions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from .reverse_project import ReverseProject, ReverseSession
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
    trace_dir = session.trace_dir
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
    return trace_dir


def reverse_launch_options(
    project_dir: str | Path,
    proxy: str | None = None,
    browser_version: str | None = None,
    trace_profile: str = "overview",
    enable_trace: bool = True,
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
    try:
        config = dict(kwargs.pop("config", None) or {})
        if enable_trace:
            trace_dir = _trace_dir(session)
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
        else:
            config.pop("propertyTrace", None)

        launch_kwargs = dict(kwargs)
        environment = dict(launch_kwargs.get("env") or os.environ)
        temporary = session.path / "runtime" / "tmp"
        temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment.update(TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
        if enable_trace:
            environment["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"
        launch_kwargs["env"] = environment
        launch_kwargs["config"] = config
        normalized_proxy = (
            _proxy_dict(proxy) if proxy is not None else launch_kwargs.get("proxy")
        )
        if proxy is not None:
            launch_kwargs["proxy"] = normalized_proxy
        if browser_version is not None:
            launch_kwargs["browser"] = browser_version

        options = launch_options(**launch_kwargs)
        session.mark_running(
            trace_profile=trace_profile,
            browser_version=browser_version or launch_kwargs.get("browser"),
            proxy=(
                {
                    "server": normalized_proxy["server"],
                    "authenticated": "username" in normalized_proxy
                    or "password" in normalized_proxy,
                }
                if normalized_proxy
                else None
            ),
        )
        return options, session
    except Exception as error:
        session.mark_incomplete(error)
        raise
