"""Opt-in real Firefox fixture. No Node test runner or shared MCP browser."""
import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import navigation, network


PAGE = b'''<!doctype html><title>initiator fixture</title><script nonce="fixture">
async function alpha() { return fetch('/echo', {method:'POST', headers:{'X-Fixture':'raw-value'}, body:'alpha-secret'}).then(r=>r.text()); }
async function beta() { return fetch(new Request('/echo', {method:'POST', body:'beta-secret'})).then(r=>r.text()); }
function gamma() { return new Promise(resolve=>{ const x=new XMLHttpRequest(); x.open('POST','/echo'); x.setRequestHeader('X-Fixture','xhr-value'); x.onload=()=>resolve(x.responseText); x.send('gamma-secret'); }); }
async function duplicateOne() { return fetch('/echo', {method:'POST',body:'identical'}).then(r=>r.text()); }
async function duplicateTwo() { return fetch('/echo', {method:'POST',body:'identical'}).then(r=>r.text()); }
window.done=Promise.all([alpha(),beta(),gamma(),duplicateOne(),duplicateTwo()]);
</script>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path in {"/", "/csp"}:
            body = PAGE
        elif self.path == "/auto":
            body = b'''<!doctype html><script>
async function departing() { await fetch('/echo', {method:'POST',body:'departing-secret'}).then(r=>r.text()); location.href='/next'; }
departing();</script>'''
        elif self.path == "/frames":
            body = b'''<!doctype html><title>frames</title><iframe src="/child"></iframe><iframe src="/child"></iframe>'''
        elif self.path == "/child":
            body = b'''<!doctype html><script>
async function childCall() { await fetch('/echo', {method:'POST',body:'child-secret'}).then(r=>r.text()); }
childCall();</script>'''
        elif self.path == "/binary":
            body = b'''<!doctype html><title>binary</title><script>
async function binaryCall() { const data=new Uint8Array(262144); for(let i=0;i<data.length;i++)data[i]=i%256;
 await fetch('/echo', {method:'POST',body:data}).then(r=>r.arrayBuffer()); document.title='binary-done'; }
binaryCall();</script>'''
        elif self.path == "/streaming":
            body = b'''<!doctype html><script>
async function streamingCall() { await fetch('/stream', {method:'POST',body:'stream-secret'}); document.title='streaming-headers'; }
streamingCall();</script>'''
        else:
            body = b"<!doctype html><title>next</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if self.path == "/csp":
            self.send_header("Content-Security-Policy", "script-src 'nonce-fixture'; connect-src 'self'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "65536")
            self.end_headers()
            self.wfile.write(b"x")
            self.wfile.flush()
            self.server.stream_release.wait(15)
            try:
                self.wfile.write(b"x" * 65535)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.mark.skipif(os.environ.get("MCP_INITIATOR_LIVE") != "1", reason="opt-in real browser")
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/csp"])
async def test_page_world_raw_stacks_survive_navigation(monkeypatch, tmp_path, capsys, path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.stream_release = Event()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager()
    monkeypatch.setattr(navigation, "browser_manager", manager)
    monkeypatch.setattr(network, "browser_manager", manager)
    project = Path(os.environ.get("MCP_INITIATOR_PROJECT", str(tmp_path / "project")))
    try:
        await manager.launch({"headless": True, "locale": "en-US", "enable_trace": False,
                              "proxy": "http://127.0.0.1:7890",
                              "browser_version": os.environ.get("MCP_INITIATOR_BROWSER_SELECTOR", "whitenightshadow/152.0.4-beta.30-reverse.7")},
                             project_dir=project)
        await network.network_capture("start", capture_body=True)
        base = f"http://127.0.0.1:{server.server_port}"
        result = await navigation.navigate(base + path, pre_inject_hooks=["xhr", "fetch"])
        assert "error" not in result
        assert result["reloaded"] is False
        page = await manager.get_active_page()
        # Do not run fixture traffic from evaluate's isolated world.
        await page.wait_for_function("document.title === 'initiator fixture'")
        for _ in range(200):
            if len([r for r in manager._network_requests if r["method"] == "POST" and r["status"]]) == 5:
                break
            await asyncio.sleep(0.025)
        posts = [dict(r) for r in manager._network_requests if r["method"] == "POST"]
        assert len(posts) == 5
        for body, function in [("alpha-secret", "alpha"), ("beta-secret", "beta"), ("gamma-secret", "gamma")]:
            req = next(r for r in posts if r["request_post_data"] == body)
            initiator = await network.get_request_initiator(req["id"])
            assert function in (initiator.get("initiator_stack") or ""), "page-world initiator absent"
            assert initiator["request_body"] == body
        for req in posts:
            if req["request_post_data"] == "identical":
                initiator = await network.get_request_initiator(req["id"])
                assert initiator["initiator_stack"] is None
                assert initiator["diagnostics"]["correlation"] == "ambiguous"
        flags = await page.evaluate("mw:() => ({fetch:__mcp_fetch_shape_log.length,xhr:__mcp_xhr_shape_log.length})")
        assert flags == {"fetch": 4, "xhr": 1}
        session = Path(manager.session.path)
        result = await navigation.navigate(base + "/next")
        assert "error" not in result
        for req in posts:
            result = await network.get_request_initiator(req["id"])
            if req["request_post_data"] == "alpha-secret":
                assert "alpha" in result["initiator_stack"]
        artifacts = list((session / "raw" / "mcp-network").rglob("*.json"))
        assert artifacts
        saved = "\n".join(p.read_text() for p in artifacts)
        assert all(value in saved for value in ("alpha-secret", "beta-secret", "gamma-secret", "raw-value", "xhr-value"))

        # A page-driven navigation cannot rely on the MCP navigate barrier.
        await navigation.navigate(base + "/auto")
        await page.wait_for_url(base + "/next")
        departing = next(r for r in manager._network_requests if r["request_post_data"] == "departing-secret")
        initiator = await network.get_request_initiator(departing["id"])
        assert "departing" in (initiator.get("initiator_stack") or "")

        await navigation.navigate(base + "/frames")
        for _ in range(200):
            children = [r for r in manager._network_requests if r["request_post_data"] == "child-secret" and r["status"]]
            if len(children) == 2:
                break
            await asyncio.sleep(0.025)
        assert len(children) == 2
        for req in children:
            initiator = await network.get_request_initiator(req["id"])
            assert "childCall" in (initiator.get("initiator_stack") or "")
        assert children[0]["frame_id"] != children[1]["frame_id"]

        await navigation.navigate(base + "/binary")
        await page.wait_for_function("document.title === 'binary-done'")
        binary = next(r for r in manager._network_requests if r["method"] == "POST")
        initiator = await network.get_request_initiator(binary["id"])
        assert "binaryCall" in (initiator.get("initiator_stack") or "")
        import base64
        expected = bytes(range(256)) * 1024
        assert base64.b64decode(initiator["request_body_base64"]) == expected
        await navigation.navigate(base + "/next")
        calls = [json.loads(p.read_text()) for p in (session / "raw/mcp-network/calls").rglob("*.json")]
        binary_calls = [c for c in calls if "binaryCall" in c.get("stack", "") and "response_body_base64" in c]
        assert binary_calls
        assert base64.b64decode(binary_calls[-1]["response_body_base64"]) == expected

        await navigation.navigate(base + "/streaming")
        await page.wait_for_function("document.title === 'streaming-headers'")
        streaming = next(r for r in manager._network_requests if r["method"] == "POST")
        initiator = await asyncio.wait_for(network.get_request_initiator(streaming["id"]), timeout=3)
        assert "streamingCall" in initiator["initiator_stack"]
        assert initiator["diagnostics"]["capture_gaps"] > 0
        gaps = [json.loads(p.read_text()) for p in (session / "raw/mcp-network/gaps").rglob("*.json")]
        assert any(g["reason"] == "pending_hook_capture" and g["pending"] > 0 for g in gaps)
        server.stream_release.set()
        assert "alpha-secret" not in capsys.readouterr().out
    finally:
        server.stream_release.set()
        if manager.runtime:
            await manager.close()
        server.shutdown()
        server.server_close()
        thread.join()
