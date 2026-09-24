from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest

from camoufox_reverse_mcp_client import (
    MCPRPCError,
    MCPStdioClient,
    MCPTimeoutError,
    MCPUnmatchedResponseError,
)


class QueueReader:
    def __init__(self) -> None:
        self._items: queue.Queue[object] = queue.Queue()

    def put_line(self, value: str) -> None:
        self._items.put(value + "\n")

    def close(self) -> None:
        self._items.put("")

    def readline(self) -> str:
        return self._items.get()


class FakeStdin:
    def __init__(self, process: "FakeProcess") -> None:
        self._process = process
        self.closed = False

    def write(self, value: str) -> int:
        message = json.loads(value)
        self._process.messages.append(message)
        self._process.handle(message)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, behavior: str = "normal") -> None:
        self.behavior = behavior
        self.messages: list[dict] = []
        self.stdout = QueueReader()
        self.stderr = QueueReader()
        self.stdin = FakeStdin(self)
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def handle(self, message: dict) -> None:
        method = message.get("method")
        if method == "initialize":
            self.stderr.put_line("server-token=do-not-print")
            self.stdout.put_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/message",
                        "params": {"message": "ready"},
                    }
                )
            )
            self.stdout.put_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "fake", "version": "1"},
                        },
                    }
                )
            )
        elif method == "notifications/initialized":
            return
        elif method == "tools/list":
            if self.behavior == "timeout":
                return
            if self.behavior == "unmatched":
                response_id = 9999
            else:
                response_id = message["id"]
            self.stdout.put_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": response_id,
                        "result": {"tools": [{"name": "echo"}]},
                    }
                )
            )
        elif method == "tools/call":
            if self.behavior == "rpc-error":
                self.stdout.put_line(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {
                                "code": -32001,
                                "message": "tool failed",
                                "data": {"token": "secret-data"},
                            },
                        }
                    )
                )
                return
            self.stdout.put_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {"content": [{"type": "text", "text": "ok"}]},
                    }
                )
            )

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.stdout.close()
        self.stderr.close()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.close()
        self.stderr.close()

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeFactory:
    def __init__(self, behavior: str = "normal") -> None:
        self.behavior = behavior
        self.process: FakeProcess | None = None
        self.argv: list[str] | None = None
        self.options: dict = {}

    def __call__(self, argv: list[str], **options):
        self.argv = argv
        self.options = options
        self.process = FakeProcess(self.behavior)
        return self.process


def test_initialize_list_tools_call_tool_and_close_use_json_lines(tmp_path: Path):
    factory = FakeFactory()
    client = MCPStdioClient(
        "fake-server",
        args=["--proxy", "http://user:proxy-secret@example.invalid"],
        env={"TOKEN": "token-secret"},
        cwd=tmp_path,
        timeout=0.2,
        popen_factory=factory,
    )

    initialized = client.initialize()
    listed = client.list_tools()
    called = client.call_tool("echo", {"value": "hello"})
    client.close()

    assert initialized["serverInfo"]["name"] == "fake"
    assert listed == {"tools": [{"name": "echo"}]}
    assert called["content"][0]["text"] == "ok"
    assert factory.argv == ["fake-server", "--proxy", "http://user:proxy-secret@example.invalid"]
    assert factory.options["env"] == {"TOKEN": "token-secret"}
    assert factory.options["cwd"] == tmp_path
    assert factory.process is not None
    assert factory.process.terminated
    assert [message["method"] for message in factory.process.messages] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]


def test_notifications_are_recorded_without_blocking_response():
    factory = FakeFactory()
    client = MCPStdioClient("fake", timeout=0.2, popen_factory=factory)

    client.initialize()

    assert client.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"message": "ready"},
        }
    ]
    assert client.drain_notifications() == []
    client.close()


def test_json_rpc_error_exposes_code_but_not_sensitive_data():
    factory = FakeFactory("rpc-error")
    client = MCPStdioClient("fake", timeout=0.2, popen_factory=factory)
    client.initialize()

    with pytest.raises(MCPRPCError) as raised:
        client.call_tool("echo")

    assert raised.value.code == -32001
    assert raised.value.message == "tool failed"
    assert "secret-data" not in str(raised.value)
    client.close()


def test_unmatched_response_id_is_rejected():
    factory = FakeFactory("unmatched")
    client = MCPStdioClient("fake", timeout=0.2, popen_factory=factory)
    client.initialize()

    with pytest.raises(MCPUnmatchedResponseError) as raised:
        client.list_tools()

    assert raised.value.received_id == 9999
    client.close()


def test_response_timeout_does_not_print_server_stderr():
    factory = FakeFactory("timeout")
    client = MCPStdioClient(
        "fake",
        args=["--token", "token-secret"],
        timeout=0.02,
        popen_factory=factory,
    )
    client.initialize()

    with pytest.raises(MCPTimeoutError):
        client.list_tools()

    assert "do-not-print" in client.server_stderr
    client.close()


def test_context_manager_closes_process_on_exception():
    factory = FakeFactory()

    with pytest.raises(RuntimeError):
        with MCPStdioClient("fake", popen_factory=factory):
            raise RuntimeError("caller error")

    assert factory.process is not None
    assert factory.process.terminated


def test_cli_list_tools_outputs_result_without_command_or_env_values(monkeypatch, capsys):
    from camoufox_reverse_mcp_client import __main__ as cli

    class FakeClient:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.instances.append(self)

        def initialize(self):
            return {"ok": True}

        def list_tools(self):
            return {"tools": [{"name": "echo"}]}

        def close(self):
            return None

    monkeypatch.setattr(cli, "MCPStdioClient", FakeClient)

    exit_code = cli.main(
        [
            "--command",
            "server-token-secret",
            "--project-dir",
            "/tmp/project",
            "--proxy",
            "http://user:proxy-secret@example.invalid",
            "--env",
            "TOKEN=token-secret",
            "list-tools",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {"tools": [{"name": "echo"}]}
    assert captured.err == ""
    assert "server-token-secret" not in captured.out + captured.err
    assert "proxy-secret" not in captured.out + captured.err
    assert "token-secret" not in captured.out + captured.err
    assert FakeClient.instances[0].kwargs["args"] == [
        "--project-dir",
        "/tmp/project",
        "--proxy",
        "http://user:proxy-secret@example.invalid",
    ]


def test_cli_call_parses_json_arguments(monkeypatch, capsys):
    from camoufox_reverse_mcp_client import __main__ as cli

    class FakeClient:
        def __init__(self, **kwargs):
            self.calls = []

        def initialize(self):
            return {}

        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"name": name, "arguments": arguments}

        def close(self):
            return None

    client = FakeClient()
    monkeypatch.setattr(cli, "MCPStdioClient", lambda **kwargs: client)

    exit_code = cli.main(
        [
            "--command",
            "fake",
            "call",
            "echo",
            "--arguments",
            '{"value": 3}',
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "name": "echo",
        "arguments": {"value": 3},
    }
    assert client.calls == [("echo", {"value": 3})]
