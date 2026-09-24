"""Small dependency-free MCP JSON Lines client.

The official MCP stdio transport uses one JSON-RPC message per line.  This
client intentionally owns only process transport and request correlation; it
does not know anything about browser automation or target-site credentials.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


class MCPError(Exception):
    """Base class for client-side MCP failures."""


class MCPProtocolError(MCPError):
    """The server returned a malformed or unsupported message."""


class MCPTimeoutError(MCPError):
    """A response was not received before the configured timeout."""


class MCPUnmatchedResponseError(MCPProtocolError):
    """The server returned a response for a different request id."""

    def __init__(self, received_id: Any) -> None:
        self.received_id = received_id
        super().__init__("MCP response id did not match the pending request")


class MCPRPCError(MCPError):
    """A JSON-RPC error without retaining its potentially sensitive data."""

    def __init__(self, code: Any, message: str) -> None:
        self.code = code
        self.message = message if isinstance(message, str) else "MCP request failed"
        super().__init__(self.message)


class MCPStdioClient:
    """Connect to an MCP server over its line-oriented stdio transport.

    ``popen_factory`` is intentionally injectable for browser-free tests.  A
    real process is started only when ``start`` or ``initialize`` is called.
    """

    def __init__(
        self,
        command: str,
        *,
        args: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.command = command
        self.args = [str(value) for value in (args or ())]
        self.env = dict(env) if env is not None else None
        self.cwd = Path(cwd) if cwd is not None else None
        self.timeout = float(timeout)
        self._popen_factory = popen_factory or subprocess.Popen
        self._process: Any | None = None
        self._stdout_queue: queue.Queue[Any] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._notifications: list[dict[str, Any]] = []
        self._reader_threads: list[threading.Thread] = []
        self._next_id = 1
        self._closed = False
        self._lock = threading.Lock()

    @property
    def process(self) -> Any | None:
        """Expose the owned process for diagnostics without exposing output."""
        return self._process

    @property
    def server_stderr(self) -> str:
        """Return captured stderr for callers that explicitly need diagnostics."""
        return "\n".join(self._stderr_lines)

    def __enter__(self) -> "MCPStdioClient":
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        self.close()

    def start(self) -> None:
        if self._process is not None:
            return
        self._closed = False
        argv = [self.command, *self.args]
        options: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
        }
        if self.env is not None:
            options["env"] = self.env
        if self.cwd is not None:
            options["cwd"] = self.cwd
        self._process = self._popen_factory(argv, **options)
        stdout = getattr(self._process, "stdout", None)
        stderr = getattr(self._process, "stderr", None)
        if stdout is None or stderr is None or getattr(self._process, "stdin", None) is None:
            self.close()
            raise MCPProtocolError("MCP stdio process has incomplete pipes")
        self._start_reader(stdout, self._stdout_queue, is_stderr=False)
        stderr_queue: queue.Queue[Any] = queue.Queue()
        self._start_reader(stderr, stderr_queue, is_stderr=True)

    def _start_reader(self, stream: Any, target: queue.Queue[Any], *, is_stderr: bool) -> None:
        def read_lines() -> None:
            try:
                while True:
                    line = stream.readline()
                    if line in ("", b""):
                        break
                    target.put(line)
            except Exception as error:
                target.put(error)
            finally:
                target.put(None)

        thread = threading.Thread(
            target=read_lines,
            name="mcp-stdio-stderr" if is_stderr else "mcp-stdio-stdout",
            daemon=True,
        )
        self._reader_threads.append(thread)
        if is_stderr:
            def collect_stderr() -> None:
                while True:
                    item = target.get()
                    if item is None:
                        return
                    if isinstance(item, BaseException):
                        continue
                    if isinstance(item, bytes):
                        item = item.decode("utf-8", "replace")
                    self._stderr_lines.append(str(item).rstrip("\r\n"))

            collector = threading.Thread(target=collect_stderr, name="mcp-stdio-stderr-log", daemon=True)
            self._reader_threads.append(collector)
            thread.start()
            collector.start()
        else:
            thread.start()

    def _write(self, message: dict[str, Any]) -> None:
        self.start()
        if self._process is None or self._closed:
            raise MCPProtocolError("MCP stdio client is closed")
        stream = self._process.stdin
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            stream.write(payload)
            stream.flush()
        except Exception as error:
            raise MCPProtocolError("MCP request could not be written") from error

    def _read_response(self, request_id: int) -> dict[str, Any]:
        try:
            while True:
                item = self._stdout_queue.get(timeout=self.timeout)
                if item is None:
                    raise MCPProtocolError("MCP server closed stdout")
                if isinstance(item, BaseException):
                    raise MCPProtocolError("MCP server stdout could not be read") from item
                if isinstance(item, bytes):
                    item = item.decode("utf-8", "replace")
                try:
                    message = json.loads(str(item))
                except (TypeError, json.JSONDecodeError) as error:
                    raise MCPProtocolError("MCP server returned invalid JSON") from error
                if not isinstance(message, dict):
                    raise MCPProtocolError("MCP server returned a non-object message")
                if "method" in message and "id" not in message:
                    self._notifications.append(message)
                    continue
                if message.get("id") != request_id:
                    raise MCPUnmatchedResponseError(message.get("id"))
                if "error" in message:
                    error = message.get("error")
                    if not isinstance(error, dict):
                        raise MCPRPCError("unknown", "MCP request failed")
                    raise MCPRPCError(error.get("code"), error.get("message", "MCP request failed"))
                result = message.get("result")
                if not isinstance(result, dict):
                    raise MCPProtocolError("MCP response result is not an object")
                return result
        except queue.Empty as error:
            raise MCPTimeoutError("MCP response timed out") from error

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(method, str) or not method:
            raise ValueError("method is required")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})})
            return self._read_response(request_id)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": dict(params or {})})

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "camoufox-reverse-python-client", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> dict[str, Any]:
        return self.request("tools/list")

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise ValueError("tool name is required")
        return self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})

    def drain_notifications(self) -> list[dict[str, Any]]:
        notifications = self._notifications[:]
        self._notifications.clear()
        return notifications

    def close(self) -> None:
        if self._process is None or self._closed:
            return
        self._closed = True
        process = self._process
        try:
            stream = getattr(process, "stdin", None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            poll = getattr(process, "poll", lambda: None)()
            if poll is None:
                terminate = getattr(process, "terminate", None)
                if callable(terminate):
                    terminate()
                try:
                    process.wait(timeout=2.0)
                except Exception:
                    kill = getattr(process, "kill", None)
                    if callable(kill):
                        kill()
                    try:
                        process.wait(timeout=2.0)
                    except Exception:
                        pass
        finally:
            self._process = None
