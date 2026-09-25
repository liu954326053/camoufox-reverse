#!/usr/bin/env python3
"""Install the bundled reverse-browser Agent Skill without touching global config."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class InstallError(ValueError):
    """Raised when an installation would cross a filesystem boundary."""


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise InstallError(f"{label} cannot be a symlink: {path}")


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise InstallError(f"{label} path contains a symlink: {current}")
        if current.parent == current:
            return
        current = current.parent


def _validate_skill_source(source: Path) -> Path:
    source = Path(source).expanduser()
    _reject_symlink(source, "source")
    if not source.is_dir():
        raise InstallError(f"source must be a directory: {source}")
    if not (source / "SKILL.md").is_file() or (source / "SKILL.md").is_symlink():
        raise InstallError("source must contain a regular SKILL.md")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise InstallError(f"source contains a symlink: {path}")
    return source.resolve(strict=True)


def _validate_target(target: Path) -> Path:
    target = Path(target).expanduser()
    _reject_symlink_ancestors(target, "target")
    if target.exists() and not target.is_dir():
        raise InstallError(f"target must be a directory: {target}")
    return target


def install_skill(
    source: str | Path,
    target: str | Path,
    *,
    dry_run: bool = True,
    force: bool = False,
) -> dict[str, object]:
    """Copy a Skill after validating every source and destination component."""
    source_path = _validate_skill_source(Path(source))
    target_path = _validate_target(Path(target))
    if target_path.exists() and not force:
        raise InstallError(f"target already exists: {target_path}")
    result = {"dry_run": dry_run, "source": str(source_path), "target": str(target_path)}
    if dry_run:
        return result
    target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_ancestors(target_path, "target")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target_path.name}.", dir=target_path.parent))
    try:
        shutil.copytree(source_path, temporary / target_path.name, symlinks=False)
        staged = temporary / target_path.name
        if target_path.exists():
            _reject_symlink(target_path, "target")
            shutil.rmtree(target_path)
        os.replace(staged, target_path)
        target_path.chmod(0o700)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return result


def _safe_proxy(proxy: str | None) -> str:
    value = proxy or "http://127.0.0.1:7890"
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        raise InstallError("proxy must be an absolute URL")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def build_mcp_config(
    project_dir: str | Path,
    proxy: str | None = None,
    *,
    command: str = "camoufox-reverse-mcp",
) -> dict[str, object]:
    """Build a portable MCP host snippet without proxy credentials."""
    project = Path(project_dir).expanduser()
    if not project.is_absolute() or ".." in project.parts:
        raise InstallError("project_dir must be an absolute path without traversal")
    if not isinstance(command, str) or not command.strip():
        raise InstallError("MCP command is required")
    args = ["--project-dir", str(project.resolve(strict=False)), "--proxy", _safe_proxy(proxy)]
    return {"mcpServers": {"camoufox-reverse": {"command": command, "args": args}}}


def write_mcp_example(
    destination: str | Path,
    config: dict[str, object],
    *,
    dry_run: bool = True,
    force: bool = False,
) -> dict[str, object]:
    """Write an explicitly requested MCP example atomically."""
    path = Path(destination).expanduser()
    _reject_symlink_ancestors(path, "example destination")
    if path.exists() and path.is_symlink():
        raise InstallError(f"example destination cannot be a symlink: {path}")
    if path.exists() and not force:
        raise InstallError(f"example destination already exists: {path}")
    result = {"dry_run": dry_run, "path": str(path)}
    if dry_run:
        return result
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_ancestors(path, "example destination")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        if path.exists():
            _reject_symlink(path, "example destination")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def _default_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    source = root / "skill"
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    target = codex_home / "skills" / "camoufox-reverse-browser"
    return source, target


def main(argv: list[str] | None = None) -> int:
    source_default, target_default = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    parser.add_argument("--source", type=Path, default=source_default)
    parser.add_argument("--skill-dir", type=Path, default=target_default)
    parser.add_argument("--mcp-config", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Actually write files; default is dry-run")
    args = parser.parse_args(argv)
    try:
        skill_result = install_skill(
            args.source,
            args.skill_dir,
            dry_run=not args.apply,
            force=args.force,
        )
        output: dict[str, object] = {"skill": skill_result, "dry_run": not args.apply}
        if args.mcp_config:
            config = build_mcp_config(args.project_dir, args.proxy)
            output["mcp"] = write_mcp_example(
                args.mcp_config,
                config,
                dry_run=not args.apply,
                force=args.force,
            )
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except (InstallError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": {"message": str(error)}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
