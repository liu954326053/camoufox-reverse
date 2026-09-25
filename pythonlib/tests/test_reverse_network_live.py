"""Opt-in real Firefox regression for bytes received before transport failure."""

import asyncio
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from camoufox.reverse_runtime import AsyncReverseBrowser


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("REVERSE_BROWSER_INTEGRATION") != "1", reason="requires installed reverse browser")
async def test_truncated_response_preserves_received_bytes(tmp_path):
    partial = b"received-before-disconnect\x00\xff"
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/partial":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(partial) + 100))
                self.end_headers()
                self.wfile.write(partial)
                self.wfile.flush()
                release.wait(timeout=10)
                self.connection.shutdown(socket.SHUT_WR)
                self.close_connection = True
            else:
                body = b"<title>Transport fixture</title>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    runtime = AsyncReverseBrowser(
        project_dir=tmp_path / "project", proxy="http://127.0.0.1:7890",
        browser_version=os.environ.get("REVERSE_BROWSER_SELECTOR"),
        enable_trace=False, headless=True, os="macos", locale="en-US",
    )
    try:
        async with runtime as active:
            await active.page.goto(f"http://127.0.0.1:{server.server_port}/")
            await active.page.evaluate("""async () => {
                const controller = new AbortController();
                const response = await fetch('/partial', {signal: controller.signal});
                await response.body.getReader().read();
                controller.abort();
            }""")
            release.set()
            await active.drain()
        rows = [json.loads(p.read_text()) for p in runtime.session.raw_dir.glob("network/*/metadata*.json")]
        row = next(r for r in rows if r["url"].endswith("/partial"))
        body_path = runtime.session.raw_dir / "network" / row["id"] / "response.body"
        assert body_path.exists(), f"received bytes lost: {row.get('response_body_error')}"
        assert body_path.read_bytes() == partial
        assert row.get("failure"), "transport failure must remain visible"
        assert row["body_availability"] == "captured_partial"
        assert row["response_complete"] is False
    finally:
        release.set()
        await runtime.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        worker.join(timeout=5)
