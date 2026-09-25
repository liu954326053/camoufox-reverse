#!/usr/bin/env python3
"""Validate reverse-browser capabilities against Google's sign-in page BotGuard VM.

Observation-only: loads the identifier page, lets the BotGuard VM run, and checks
that network evidence, initiator stacks, scripts, native trace events and state
all land in the project session. Never submits credentials or identifiers.
"""

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
sys.path.insert(0, str(ROOT))  # adapters.google_botguard
from camoufox_reverse_mcp_client import MCPStdioClient
from camoufox.reverse_compat import BROWSER_SELECTOR
from adapters.google_botguard.handler_patch import (
    handler_count_patches,
    write_handler_counts,
)

# Bare GET /v3/signin/identifier returns Google's generic 400 page; the real
# entry point is ServiceLogin, which 302s into identifier with flow params.
DIRECT_URL = "https://accounts.google.com/ServiceLogin?hl=en-US"
DOLA_URL = "https://www.dola.com/chat/"


def unpack(reply):
    values = [json.loads(item["text"]) for item in reply.get("content", []) if item.get("type") == "text"]
    value = values[0] if len(values) == 1 else values
    if reply.get("isError") or isinstance(value, dict) and value.get("error"):
        raise RuntimeError(f"MCP tool failed: {json.dumps(value, ensure_ascii=False)[:400]}")
    return value


def run(args):
    project = args.project_dir.resolve()
    project.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_dir = project / "validation" / uuid.uuid4().hex
    report_dir.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    transcript = []
    summary = {"status": "failed", "report_dir": str(report_dir), "flow": args.flow,
               "target_url": DOLA_URL if args.flow == "dola" else DIRECT_URL}
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "pythonlib"), str(ROOT / "integrations/camoufox-reverse-mcp/src"),
         environment.get("PYTHONPATH", "")])
    with MCPStdioClient(
        args.python,
        args=["-m", "camoufox_reverse_mcp", "--project-dir", str(project),
              "--proxy", args.proxy, "--headless", "--os", "macos", "--locale", "en-US"],
        env=environment, timeout=120,
    ) as client:
        client.initialize()

        def call(name, **arguments):
            (report_dir / "progress.json").write_text(json.dumps({"tool": name, "state": "running"}))
            reply = client.call_tool(name, arguments)
            transcript.append({"tool": name, "args": arguments, "result": reply})
            (report_dir / "mcp-transcript.json").write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2))
            (report_dir / "progress.json").write_text(json.dumps({"tool": name, "state": "returned"}))
            return unpack(reply)

        launched = False
        try:
            launch = call("launch_browser", project_dir=str(project), proxy=args.proxy,
                          browser_version=args.browser_version, headless=True, os_type="macos",
                          locale="en-US", enable_trace=True, trace_profile="deep",
                          capture_profile="raw")
            launched = True
            summary["session_id"] = launch["session_id"]
            summary["session_dir"] = launch["session_dir"]
            call("network_capture", action="start", capture_body=True)
            if args.vm_loop_trace:
                # Phase-2 Task 2: instrument while(true) dispatch loops in the
                # identifier document before it loads (route persists at
                # context level across the Dola -> Google redirect chain).
                # Phase-3: state variables are auto-detected per loop
                # (while(x!=N) test, trailing switch(X), for(init;;) FSM
                # discriminant); hardcoded guesses only pollute snapshots.
                call("vm_loop_trace", action="install",
                     url_pattern="**/v3/signin/identifier*",
                     tick_times=True,
                     # Phase-4 Task 2: 全量落盘（148k 解码器循环不截断）
                     max_states_per_loop=200000,
                     # Phase-4 Task 5: handler 注册/派发计数补丁（通用通道，
                     # 补丁定义在 adapters/google_botguard/handler_patch.py）
                     code_patches=(handler_count_patches()
                                   if args.handler_counts else None))
            if args.flow == "dola":
                # Real user chain: Dola -> Log In -> Continue with Google ->
                # accounts.google.com identifier page (OAuth flow params attached).
                call("navigate", url=DOLA_URL, pre_inject_hooks=["xhr", "fetch"],
                     clear_network_capture=False)
                call("click", selector="button:has-text('Log In')")
                call("click", selector="button:has-text('Continue with Google')")
            else:
                call("navigate", url=DIRECT_URL, pre_inject_hooks=["xhr", "fetch"],
                     clear_network_capture=False)
            call("wait_for", selector="input#identifierId", timeout=45000)
            info = call("get_page_info")
            summary["identifier_page_reached"] = (
                urlsplit(info.get("url", "")).hostname == "accounts.google.com")
            summary["page_title"] = info.get("title", "")[:80]

            # Let the BotGuard VM initialize and run its program.
            call("evaluate_js", expression="new Promise(r => setTimeout(r, 8000))",
                 await_promise=True)

            # BotGuard's VM program (/js/bg/...) and the B4hajb RPC only fire on
            # identifier submission. A random non-existent identifier triggers
            # both without touching a real account or any credential step; the
            # page answers "Couldn't find your Google Account" and we stop.
            if args.submit_identifier:
                call("type_text", selector="input#identifierId",
                     text=args.submit_identifier, delay=40)
                call("click", selector="button:has-text('Next')")
                call("evaluate_js", expression="new Promise(r => setTimeout(r, 10000))",
                     await_promise=True)
                summary["identifier_submitted"] = True

            # Hook-state verification: raw network hooks must actually be live
            # in the page main world (mw: prefix), not the utility world.
            def probe(name, fn):
                try:
                    summary[name] = fn()
                except Exception as error:
                    summary[name] = {"probe_error": f"{type(error).__name__}: {error}"[:300]}
                return summary[name]

            # mw: accepts a function (invoked by the evaluator), not an IIFE.
            probe("hook_state", lambda: call("evaluate_js", expression=(
                "mw:() => ({xhr_raw: !!window.__mcp_xhr_hooked, fetch_raw: !!window.__mcp_fetch_hooked,"
                " raw_state: !!window.__mcp_raw_network_state,"
                " botguard_loaded: typeof botguard !== 'undefined' && !!botguard.bg,"
                " xhr_calls: (window.__mcp_xhr_log || []).length,"
                " fetch_calls: (window.__mcp_fetch_log || []).length})")))

            if args.vm_loop_trace:
                def drain_loops():
                    result = call("vm_loop_trace", action="log")
                    return {
                        "loops_observed": result.get("loops_observed"),
                        "total_iterations": result.get("total_iterations"),
                        "overhead": result.get("overhead"),
                        "artifact": result.get("artifact"),
                        "loops": [{k: l.get(k) for k in
                                   ("loop", "iterations", "states_recorded", "truncated")}
                                  for l in result.get("loops", [])][:10],
                    }
                probe("vm_loop_trace", drain_loops)
                # iframe realm 普查：BotGuard 把 VM 放到 iframe realm 执行，
                # 这里确认 iframe 的 sandbox/src 与运行时是否装入。
                probe("iframe_census", lambda: call("evaluate_js", expression=(
                    "mw:() => Array.prototype.map.call("
                    "document.querySelectorAll('iframe'), function(f){"
                    " var tick; try { tick = !!(f.contentWindow && f.contentWindow.__mcp_vm_loop_tick); }"
                    " catch(e) { tick = 'cross-origin'; }"
                    " return {src: String(f.src||'').substring(0,120),"
                    " sandbox: String(f.sandbox||''), tick: tick};})")))

            def list_scripts():
                scripts = call("scripts", action="list")
                if isinstance(scripts, dict):
                    scripts = scripts.get("scripts", scripts.get("result", []))
                return scripts

            scripts = probe("scripts_probe", list_scripts) or []
            if isinstance(scripts, dict):
                scripts = []
            summary["scripts_total"] = len(scripts)
            botguard = [s for s in scripts
                        if any(k in str(s.get("url", "")).lower()
                               for k in ("/js/bg/", "botguard", "bgdata"))]
            summary["botguard_scripts"] = [s.get("url", "")[:160] for s in botguard]

            requests = call("list_network_requests")
            if isinstance(requests, dict):
                requests = requests.get("result", [requests])
            summary["requests_total"] = len(requests)
            # The BotGuard VM program arrives as a script request (/js/bg/...),
            # not as a DOM-listed script.
            bg_requests = [r for r in requests if "/js/bg/" in r.get("url", "")]
            summary["botguard_program_requests"] = [
                {"id": r["id"], "url": r.get("url", "")[:160], "status": r.get("status")}
                for r in bg_requests]
            interesting = [r for r in requests
                           if urlsplit(r.get("url", "")).hostname in
                           ("accounts.google.com", "www.google.com", "ssl.gstatic.com",
                            "www.gstatic.com", "play.google.com")
                           and r.get("type") in ("xhr", "fetch")]
            b4hajb = [r for r in requests if "B4hajb" in r.get("url", "")]
            summary["b4hajb_requests"] = len(b4hajb)
            summary["b4hajb_urls"] = [r.get("url", "")[:160] for r in b4hajb]
            summary["sampled_xhr_fetch"] = len(interesting)

            sample_ids = [r["id"] for r in (b4hajb + bg_requests + interesting)[:10]]
            seen = set()
            initiators = []
            for rid in sample_ids:
                if rid in seen:
                    continue
                seen.add(rid)
                try:
                    initiators.append(call("get_request_initiator", request_id=rid))
                except Exception as error:
                    initiators.append({"request_id": rid, "error": type(error).__name__})
            stacked = [i for i in initiators if i.get("initiator_stack")]
            summary["initiator_queries"] = len(initiators)
            summary["initiator_with_stack"] = len(stacked)
            summary["initiator_correlations"] = dict(Counter(
                i.get("diagnostics", {}).get("correlation", "error" if i.get("error") else "?")
                for i in initiators))
            (report_dir / "initiators.json").write_text(
                json.dumps(initiators, ensure_ascii=False, indent=2))

            probe("trace_files", lambda: call("list_trace_files"))
            probe("environment", lambda: sorted(call("check_environment").keys())[:20])
        except Exception as error:
            summary["exception_type"] = type(error).__name__
            summary["exception"] = str(error)[:300]
        finally:
            if launched:
                try:
                    closed = call("close_browser")
                    summary["capture_status"] = closed.get("status")
                    capture = closed.get("capture", {})
                    summary["capture_errors"] = dict(Counter(
                        e.get("stage", "?") for e in capture.get("errors", [])))
                    summary["capture_stats"] = capture.get("stats")
                except Exception as error:
                    summary["close_exception_type"] = type(error).__name__
            (report_dir / "mcp-transcript.json").write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2))
            (report_dir / "server-stderr.log").write_text(client.server_stderr)

    if summary.get("session_dir"):
        session = Path(summary["session_dir"])
        manifest = json.loads((session / "manifest.json").read_text())
        summary["manifest_status"] = manifest.get("status")
        trace_dir = session / "trace"
        summary["native_trace_files"] = len(list(trace_dir.rglob("*.jsonl"))) if trace_dir.exists() else 0
        summary["native_events"] = sum(sum(1 for _ in p.open()) for p in trace_dir.rglob("*.jsonl")) \
            if trace_dir.exists() else 0
        sidecars = list(trace_dir.rglob("*.meta.json")) if trace_dir.exists() else []
        summary["native_sidecars"] = len(sidecars)
        summary["native_dropped"] = sum(json.loads(p.read_text()).get("dropped", -1) for p in sidecars)
        index_path = project / "indexes" / f"{summary['session_id']}.json"
        if index_path.exists():
            index = json.loads(index_path.read_text())
            summary["index_files"] = len(index.get("files", index.get("artifacts", [])))
            summary["event_loss"] = index.get("event_loss")

        # Post-close raw-evidence analysis: sign-in RPC correlation and the
        # BotGuard VM proof token inside the request body. MI613e is the
        # identifier-lookup RPC in the current flow; B4hajb appears at later
        # steps. The VM proof is a long '!'-prefixed string inside f.req.
        import re
        from urllib.parse import parse_qsl, unquote

        def find_vm_proof(node):
            if isinstance(node, list):
                for value in node:
                    found = find_vm_proof(value)
                    if found:
                        return found
            elif isinstance(node, str):
                if node.startswith("!") and len(node) > 500:
                    return node
                # nested JSON strings inside the batchexecute envelope
                if node.startswith("[") and len(node) > 100:
                    try:
                        return find_vm_proof(json.loads(node))
                    except Exception:
                        return None
            return None

        rpcids = set()
        proof = None
        calls_dir = session / "raw" / "mcp-network" / "calls"
        for call_file in calls_dir.glob("*/*.json") if calls_dir.exists() else []:
            try:
                record = json.loads(call_file.read_text())
            except Exception:
                continue
            url = record.get("url", "")
            if "accounts.google.com" not in url or "batchexecute" not in url:
                continue
            match = re.search(r"rpcids=([A-Za-z0-9,_-]+)", url)
            if match:
                rpcids.update(match.group(1).split(","))
            if proof is None and record.get("body"):
                fields = dict(parse_qsl(str(record["body"])))
                if "f.req" in fields:
                    try:
                        proof = find_vm_proof(json.loads(fields["f.req"]))
                    except Exception:
                        pass
        summary["signin_rpcids"] = sorted(rpcids)
        summary["botguard_proof_observed"] = proof is not None
        if proof:
            summary["botguard_proof_length"] = len(proof)
            summary["botguard_proof_prefix"] = proof[:12]

        # Phase-4 Task 5: handler 注册/派发频次表（补丁计数器随 drain 回收后落盘）
        if args.handler_counts:
            counts_path = write_handler_counts(session)
            counts = json.loads(counts_path.read_text())
            summary["handler_counts"] = {
                "level": counts.get("level"),
                "registered": counts.get("registered"),
                "dispatched": counts.get("dispatched"),
                "artifact": str(counts_path),
            }

    passed = (
        summary.get("identifier_page_reached")
        and summary.get("manifest_status") in ("complete", "incomplete")
        and summary.get("initiator_with_stack", 0) > 0
        and summary.get("native_events", 0) > 0
        and summary.get("native_dropped") == 0)
    if args.submit_identifier:
        passed = passed and summary.get("botguard_proof_observed")
    summary["status"] = "passed" if passed else "failed"
    (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    parser.add_argument("--browser-version", default=BROWSER_SELECTOR)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--flow", choices=["dola", "direct"], default="dola",
                        help="dola: enter Google sign-in through dola.com OAuth; "
                             "direct: ServiceLogin entry")
    parser.add_argument("--submit-identifier", default=None,
                        help="random non-existent identifier used to trigger the "
                             "BotGuard VM program and sign-in RPC; omit to stay passive")
    parser.add_argument("--vm-loop-trace", action="store_true",
                        help="instrument while(true) dispatch loops in the "
                             "identifier document (phase-2 Task 2)")
    parser.add_argument("--handler-counts", action="store_true",
                        help="Phase-4 Task 5: install BotGuard handler "
                             "register/dispatch counting patches via the "
                             "generic code_patches channel and write "
                             "derived/handler-counts.json after the run")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
