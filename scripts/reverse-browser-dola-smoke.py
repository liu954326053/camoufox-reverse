#!/usr/bin/env python3
"""Exercise the installed MCP server on Dola's Google sign-in entry, without credentials."""

import argparse
import json
import os
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "pythonlib"))
from camoufox_reverse_mcp_client import MCPStdioClient
from camoufox.reverse_compat import BROWSER_SELECTOR


def unpack(reply):
    values = [json.loads(item["text"]) for item in reply.get("content", []) if item.get("type") == "text"]
    value = values[0] if len(values) == 1 else values
    if reply.get("isError") or isinstance(value, dict) and value.get("error"):
        raise RuntimeError("MCP tool failed; inspect private transcript")
    return value


def run(args):
    project = args.project_dir.resolve()
    project.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_dir = project / "validation" / uuid.uuid4().hex
    report_dir.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    transcript = []
    summary = {"status": "failed", "report_dir": str(report_dir)}
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "pythonlib"), str(args.mcp_source / "src"), environment.get("PYTHONPATH", "")])
    with MCPStdioClient(
        args.python, args=["-m", "camoufox_reverse_mcp", "--project-dir", str(project),
                           "--proxy", args.proxy, "--headless", "--os", "macos", "--locale", "en-US"],
        env=environment, timeout=90,
    ) as client:
        client.initialize()

        def call(name, **arguments):
            (report_dir / "progress.json").write_text(json.dumps({"tool": name, "state": "running"}))
            reply = client.call_tool(name, arguments)
            transcript.append({"tool": name, "result": reply})
            (report_dir / "mcp-transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
            (report_dir / "progress.json").write_text(json.dumps({"tool": name, "state": "returned"}))
            return unpack(reply)

        launched = False
        try:
            launch = call("launch_browser", project_dir=str(project), proxy=args.proxy,
                          browser_version=args.browser_version, headless=True, os_type="macos", locale="en-US",
                          enable_trace=True, trace_profile="deep", capture_profile="raw")
            launched = True
            summary["session_id"] = launch["session_id"]
            summary["session_dir"] = launch["session_dir"]
            call("network_capture", action="start", capture_body=True)
            call("navigate", url="https://www.dola.com/chat/", pre_inject_hooks=["xhr", "fetch"], clear_network_capture=False)
            call("click", selector="button:has-text('Log In')")
            requests = call("list_network_requests", resource_type="xhr")
            if isinstance(requests, dict):
                requests = requests.get("result", [requests])
            candidates = [r for r in requests if urlsplit(r.get("url", "")).hostname == "www.dola.com"]
            sample_ids = [r["id"] for r in candidates[:8]]
            before = [call("get_request_initiator", request_id=rid) for rid in sample_ids]
            summary["stacks_before_navigation"] = sum(bool(r.get("initiator_stack")) for r in before)
            call("click", selector="button:has-text('Continue with Google')")
            info = call("get_page_info")
            summary["google_host_reached"] = urlsplit(info.get("url", "")).hostname == "accounts.google.com"
            after = [call("get_request_initiator", request_id=rid) for rid in sample_ids]
            summary["stacks_after_navigation"] = sum(bool(r.get("initiator_stack")) for r in after)
            summary["stacks_preserved"] = bool(sample_ids) and any(
                a.get("initiator_stack") and a.get("initiator_stack") == b.get("initiator_stack")
                for a, b in zip(before, after)
            )
            # Wait on a visible field instead of sleeping for page load.
            call("wait_for", selector="input#identifierId", timeout=30000)
            info = call("get_page_info")
            summary["google_identifier_reached"] = urlsplit(info.get("url", "")).hostname == "accounts.google.com"
        except Exception as error:
            summary["exception_type"] = type(error).__name__
        finally:
            if launched:
                try:
                    closed = call("close_browser")
                    summary["capture_status"] = closed.get("status")
                    summary["capture_errors"] = dict(Counter(e["stage"] for e in closed.get("capture", {}).get("errors", [])))
                except Exception as error:
                    summary["close_exception_type"] = type(error).__name__
            (report_dir / "mcp-transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
            (report_dir / "server-stderr.log").write_text(client.server_stderr)
    if summary.get("session_dir"):
        session = Path(summary["session_dir"])
        summary["native_events"] = sum(sum(1 for _ in p.open()) for p in (session / "trace").rglob("*.jsonl"))
        index = json.loads((project / "indexes" / f"{summary['session_id']}.json").read_text())
        summary["event_loss"] = index["event_loss"]
        summary["event_loss_complete"] = index["event_loss_complete"]
    summary["request_initiator_chain_verified"] = bool(
        summary.get("google_identifier_reached") and summary.get("stacks_preserved")
    )
    if (summary.get("capture_status") == "closed" and summary.get("google_identifier_reached")
            and summary.get("stacks_preserved") and summary.get("native_events", 0) > 0
            and summary.get("event_loss") == 0 and summary.get("event_loss_complete")):
        summary["status"] = "verified"
    (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "verified" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--mcp-source", type=Path,
                        default=ROOT / "integrations/camoufox-reverse-mcp")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    parser.add_argument("--browser-version", default=BROWSER_SELECTOR)
    raise SystemExit(run(parser.parse_args()))
