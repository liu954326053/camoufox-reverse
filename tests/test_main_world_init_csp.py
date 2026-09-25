"""Opt-in browser acceptance: prefixed init runs while page CSP still blocks eval."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


BODY = b'''<!doctype html><title>pending</title><script nonce="fixture">
let evalBlocked = false;
try { eval('window.__evalRan = true'); } catch (error) { evalBlocked = true; }
document.title = JSON.stringify({
  mainRuns: window.__mainRuns || 0,
  privateVisible: Boolean(window.__privateMarker),
  evalBlocked,
  evalRan: Boolean(window.__evalRan)
});
</script>'''


@pytest.mark.skipif(not os.environ.get("CAMOUFOX_INIT_TEST_EXECUTABLE"),
                    reason="set CAMOUFOX_INIT_TEST_EXECUTABLE to test the packaged browser")
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("waived_fallback", [False, True])
def test_prefixed_init_runs_under_strict_csp_without_enabling_page_eval(enabled, waived_fallback):
    from playwright.sync_api import sync_playwright

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Security-Policy", "script-src 'nonce-fixture'; connect-src 'self'")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("CAMOU_CONFIG")}
    environment["CAMOU_CONFIG_1"] = json.dumps({"allowMainWorld": enabled})
    body = "window.__mainRuns = (window.__mainRuns || 0) + 1;"
    if waived_fallback:
        # Same dispatch shape as MCP network_evidence.init_script. Under CSP the
        # sandbox workaround must not be needed; the native main world runs it.
        body = ("(function() { if (window !== document.defaultView) { "
                "document.defaultView.wrappedJSObject.eval(" + json.dumps(body) + "); return; }\n"
                + body + "\n})();")
    try:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(
                executable_path=os.environ["CAMOUFOX_INIT_TEST_EXECUTABLE"],
                headless=True, env=environment,
                proxy={"server": "http://127.0.0.1:7890"},
            )
            try:
                context = browser.new_context()
                context.add_init_script("mw:" + body)
                context.add_init_script("window.__privateMarker = true;")
                page = context.new_page()
                for path in ("first", "second"):
                    page.goto(f"http://127.0.0.1:{server.server_port}/{path}")
                    for reload in (False, True):
                        if reload:
                            page.reload()
                        observed = json.loads(page.title())
                        assert observed == {
                            "mainRuns": 1 if enabled else 0,
                            "privateVisible": False,
                            "evalBlocked": True,
                            "evalRan": False,
                        }
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
