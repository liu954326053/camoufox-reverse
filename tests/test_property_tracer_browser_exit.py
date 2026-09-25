"""Opt-in native lifecycle regression against an explicitly selected browser.

CAMOUFOX_EXIT_TEST_EXECUTABLE selects the binary; CAMOUFOX_EXIT_TEST_PROJECT_DIR
retains a unique synthetic run for inspection. No network collector is involved.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import uuid
from pathlib import Path


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@unittest.skipUnless(os.environ.get("CAMOUFOX_EXIT_TEST_EXECUTABLE"),
                     "set CAMOUFOX_EXIT_TEST_EXECUTABLE for the real-browser regression")
class PropertyTracerBrowserExitTests(unittest.TestCase):
    def test_reload_navigation_and_content_exit_finalize_every_producer(self):
        from playwright.sync_api import sync_playwright

        project = Path(os.environ["CAMOUFOX_EXIT_TEST_PROJECT_DIR"]).resolve()
        run = project / uuid.uuid4().hex
        traces = run / "trace"
        traces.mkdir(parents=True)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("CAMOU_CONFIG")}
        environment.update({
            "CAMOU_CONFIG_1": json.dumps({"propertyTrace": {
                "enabled": True, "logDir": str(traces), "maxEventsPerSession": 100000,
            }}),
            "MOZ_DISABLE_CONTENT_SANDBOX": "1",
        })
        retired = set()
        observations = []
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(
                executable_path=os.environ["CAMOUFOX_EXIT_TEST_EXECUTABLE"],
                headless=True, env=environment,
                proxy={"server": "http://127.0.0.1:7890"},
                firefox_user_prefs={
                    "dom.ipc.processPrelaunch.enabled": False,
                    "dom.ipc.keepProcessesAlive.web": 0,
                    "browser.sessionhistory.max_total_viewers": 0,
                    "fission.autostart": True,
                },
            )
            try:
                for iteration in range(3):
                    context = browser.new_context()
                    # Real documents and process switches, deterministic page content.
                    context.route("https://*.example.test/**", lambda route: route.fulfill(
                        content_type="text/html", body="<title>Native exit fixture</title>"))
                    page = context.new_page()
                    for site in ("a", "b"):
                        page.goto(f"https://{site}{iteration}.example.test/")
                        for _ in range(2):
                            page.evaluate("[navigator.userAgent, navigator.platform, screen.width]")
                            page.reload()
                    context.close()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        pids = {int(path.name.split("_")[0])
                                for path in (traces / "traces").glob("*.jsonl")}
                        newly_retired = {pid for pid in pids if not process_alive(pid)} - retired
                        if newly_retired:
                            retired.update(newly_retired)
                            break
                        time.sleep(0.05)
                    observations.append({"iteration": iteration, "retired": sorted(retired)})
            finally:
                browser.close()

        files = sorted((traces / "traces").glob("*.jsonl"))
        problems = []
        total_events = 0
        for trace in files:
            pid, session = map(int, trace.stem.split("_"))
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            total_events += len(events)
            try:
                meta = json.loads(Path(str(trace) + ".meta.json").read_text())
                assert meta == {"state": "off", "session_id": session,
                                "events": len(events), "dropped": 0}, meta
                assert [event["q"] for event in events] == list(range(len(events)))
                status = (traces / "control" / f"status-{pid}.state").read_text().split()
                assert status == ["off", str(session), f"events={len(events)}", "dropped=0"], status
            except (OSError, ValueError, AssertionError) as error:
                problems.append({"trace": trace.name, "events": len(events), "error": str(error)})
        summary = {"executable": os.environ["CAMOUFOX_EXIT_TEST_EXECUTABLE"],
                   "retired_before_browser_close": sorted(retired),
                   "observations": observations, "traces": len(files),
                   "events": total_events, "problems": problems}
        (run / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
        self.assertTrue(retired, f"no content process retired during navigation: {run}")
        self.assertGreater(total_events, 0, f"no native events: {run}")
        self.assertFalse(problems, f"native exit failed: {run}\n{problems}")


if __name__ == "__main__":
    unittest.main()
