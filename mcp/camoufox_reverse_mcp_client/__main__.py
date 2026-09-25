"""Command line entrypoint for the dependency-free MCP client."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .client import MCPError, MCPStdioClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Camoufox Reverse MCP stdio client")
    parser.add_argument("--command", required=True, help="MCP server executable")
    parser.add_argument("--project-dir", help="Absolute project directory forwarded to the server")
    parser.add_argument("--proxy", help="Proxy URL forwarded to the server")
    parser.add_argument("--cwd", type=Path, help="MCP server working directory")
    parser.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--start-retries",
        type=int,
        default=2,
        help="Transparent respawn attempts when the server process dies during "
        "the initialize handshake (default: 2; 0 disables)",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list-tools", help="List MCP tools")
    call = subparsers.add_parser("call", help="Call one MCP tool")
    call.add_argument("name")
    call.add_argument("--arguments", default="{}", help="JSON object passed as tool arguments")
    return parser


def _parse_env(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if not separator or not name:
            raise ValueError("--env must use NAME=VALUE")
        result[name] = content
    return result


def _server_args(namespace: argparse.Namespace) -> list[str]:
    args: list[str] = []
    if namespace.project_dir is not None:
        args.extend(["--project-dir", namespace.project_dir])
    if namespace.proxy is not None:
        args.extend(["--proxy", namespace.proxy])
    return args


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        namespace = parser.parse_args(argv)
        supplied_env = _parse_env(namespace.env)
        env = {**os.environ, **supplied_env} if supplied_env else None
        client = MCPStdioClient(
            command=namespace.command,
            args=_server_args(namespace),
            env=env,
            cwd=namespace.cwd,
            timeout=namespace.timeout,
            start_retries=namespace.start_retries,
        )
        try:
            client.initialize()
            if namespace.action == "list-tools":
                result: dict[str, Any] = client.list_tools()
            else:
                try:
                    arguments = json.loads(namespace.arguments)
                except json.JSONDecodeError as error:
                    raise ValueError("--arguments must be a JSON object") from error
                if not isinstance(arguments, dict):
                    raise ValueError("--arguments must be a JSON object")
                result = client.call_tool(namespace.name, arguments)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        finally:
            client.close()
    except (MCPError, OSError, ValueError) as error:
        message = (
            "MCP request failed; details omitted to protect sensitive values."
            if isinstance(error, MCPError)
            else str(error)
        )
        print(json.dumps({"status": "error", "error": {"message": message}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
