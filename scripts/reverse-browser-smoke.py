#!/usr/bin/env python3
"""Exercise the owned runtime against a local deterministic HTTP fixture."""

import argparse
import asyncio
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pythonlib"))

from camoufox.reverse_runtime import AsyncReverseBrowser


BODY = bytes(range(256)) * 1024
SCRIPT = b"""
window.fixtureReady = false;
localStorage.setItem('raw-fixture', 'unredacted-value');
sessionStorage.setItem('session-fixture', 'session-raw-value');
window.observedEnvironment = [navigator.userAgent, screen.width, innerWidth];
Promise.all([
 fetch('/same', {method:'POST', body:'first-body'}).then(r=>r.arrayBuffer()),
 fetch('/same', {method:'POST', body:'second-body'}).then(r=>r.arrayBuffer()),
 fetch('/redirect').then(r=>r.text())
]).then(()=>{window.fixtureReady=true;document.documentElement.setAttribute('data-fixture-ready','yes');});
"""


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, content, mime="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Set-Cookie", "first=raw-first; Path=/")
        self.send_header("Set-Cookie", "second=raw-second; Path=/")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/app.js":
            self.reply(SCRIPT, "application/javascript")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/done")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/done":
            self.reply(b"redirect-complete", "text/plain")
        else:
            self.reply(b'<html><title>Reverse fixture</title><script src="/app.js"></script></html>', "text/html")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.reply(BODY)


async def exercise(args):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = AsyncReverseBrowser(
        project_dir=args.project_dir,
        proxy=args.proxy,
        browser_version=args.browser_version,
        enable_trace=args.trace,
        trace_profile="deep",
        headless=True,
        os="macos" if sys.platform == "darwin" else "linux",
        locale="en-US",
    )
    try:
        async with runtime as active:
            await active.page.goto(f"http://127.0.0.1:{server.server_port}/")
            # Check readiness via the DOM/API rather than assuming network idle.
            for _ in range(100):
                if await active.page.evaluate("document.documentElement.getAttribute('data-fixture-ready') === 'yes'"):
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("fixture did not complete")
            await active.drain()
        root = runtime.session.path
        sources = list((root / "raw").rglob("*"))
        network_bodies = [path.read_bytes() for path in sources if path.name == "response.body"]
        request_bodies = [path.read_bytes() for path in sources if path.name == "request.body"]
        assert sum(body == BODY for body in network_bodies) == 2, "binary response capture lost bytes or same-URL requests"
        assert b"first-body" in request_bodies and b"second-body" in request_bodies
        assert any(path.read_bytes() == SCRIPT for path in (root / "raw/scripts").glob("*.js")), "script source missing"
        snapshots = list((root / "raw").rglob("*state*.json"))
        assert snapshots, "storage snapshot missing"
        assert any(b"raw-first" in path.read_bytes() for path in snapshots), "cookie snapshot missing"
        manifest = runtime.session.manifest_snapshot()
        assert manifest["status"] == "complete", manifest["status"]
        index = json.loads((runtime.session.project.indexes_dir / f"{runtime.session.session_id}.json").read_text())
        assert index["files"], "index omitted captured files"
        traces = list((root / "trace").rglob("*.jsonl"))
        if args.trace:
            assert traces and any(path.stat().st_size for path in traces), "native trace produced no events"
        print(json.dumps({
            "status": "verified", "session_dir": str(root), "files": len(index["files"]),
            "binary_response_bytes": len(BODY), "binary_sha256": hashlib.sha256(BODY).hexdigest(),
            "native_trace_files": len(traces), "event_loss": index["event_loss"],
        }))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    parser.add_argument("--browser-version")
    parser.add_argument("--trace", action="store_true")
    asyncio.run(exercise(parser.parse_args()))
