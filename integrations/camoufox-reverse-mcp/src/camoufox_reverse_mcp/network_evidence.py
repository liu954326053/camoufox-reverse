"""Raw, session-owned request evidence. Ambiguous calls are never guessed."""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path
from urllib.parse import urldefrag


BRIDGE = """(() => {
    if (window.__mcp_raw_bridge) return;
    window.__mcp_raw_bridge = true;
    window.addEventListener('__mcp_raw_network', event => {
        if (typeof event.detail !== 'string') return;
        window.__mcp_network_evidence(event.detail).catch(() => {});
    });
    window.dispatchEvent(new Event('__mcp_raw_ready'));
})();"""


def network_hook_script(preset: str, content: str) -> str:
    if preset not in {"xhr", "fetch"}:
        return content
    helper = (Path(__file__).parent / "hooks" / "network_raw.js").read_text()
    return "mw:" + helper + "\n" + content


def init_script(content: str) -> str:
    if not content.startswith("mw:"):
        return content
    source = content[3:]
    # Playwright's __pwInitScripts guard precedes our source. reverse.6's
    # prefix parser consequently treats mw: as a label in the sandbox. The
    # waived Window eval runs before page JS; keep the native path for builds
    # that correctly recognize the prefix. No driver/native files are patched.
    return ("mw:(function() { if (window !== document.defaultView) { "
            "document.defaultView.wrappedJSObject.eval(" + json.dumps(source) + "); return; }\n"
            + source + "\n})();")


class NetworkEvidence:
    def __init__(self, manager):
        self.manager = manager
        self.frames = {}
        self.documents = {}
        self.document_artifacts = {}
        self.calls = {}
        self.requests = {}
        self.by_id = {}
        self.saved = {}
        self.errors = []
        self.contexts = set()
        self.checkpoint_timeout = 5.0
        self.gaps = []
        self._gap_keys = set()

    def frame_id(self, frame):
        return self.frames.setdefault(frame, uuid.uuid4().hex)

    def write(self, category, identity, value):
        data = json.dumps(value, ensure_ascii=True, sort_keys=True).encode()
        key = (category, identity)
        if self.saved.get(key) == data:
            return
        try:
            self.manager.write_artifact(
                None, data, "mcp-network-" + category,
                default=f"raw/mcp-network/{category}/{identity}/{uuid.uuid4().hex}.json",
            )
        except Exception as error:
            self.errors.append(type(error).__name__)
            raise
        self.saved[key] = data

    def receive(self, source, payload):
        event = json.loads(payload) if isinstance(payload, str) else payload
        frame = self.frame_id(source["frame"])
        document = event["document_id"]
        self.documents[frame] = document
        if event["type"] == "document":
            identity = self.document_artifacts.setdefault((frame, document), uuid.uuid4().hex)
            self.write("documents", identity, {**event, "frame_id": frame})
            return
        call = {**event, "frame_id": frame}
        # Call IDs are opaque data; never use page-controlled IDs as paths.
        key = (frame, document, event["call_id"])
        previous = self.calls.get(key)
        if previous and previous.get("revision", 0) > event.get("revision", 0):
            return
        artifact_id = previous["artifact_id"] if previous else uuid.uuid4().hex
        call["artifact_id"] = artifact_id
        self.calls[key] = call
        self.write("calls", artifact_id, call)

    async def install(self, context):
        if context in self.contexts:
            return
        await context.expose_binding("__mcp_network_evidence", self.receive)
        await context.add_init_script(script=BRIDGE)
        for page in context.pages:
            for frame in page.frames:
                await frame.evaluate(BRIDGE)
        self.contexts.add(context)

    def record_request(self, request, entry):
        frame = self.frame_id(request.frame)
        entry["frame_id"] = frame
        entry["document_id"] = self.documents.get(frame)
        body = request.post_data_buffer
        entry["request_body_base64"] = base64.b64encode(body).decode() if body is not None else None
        entry["evidence_id"] = uuid.uuid4().hex
        runtime = self.manager.runtime
        if callable(getattr(runtime, "_request_id", None)):
            entry["runtime_request_id"] = runtime._request_id(request)
        self.requests[request] = entry
        self.by_id[entry["id"]] = entry
        self.write("requests", entry["evidence_id"], entry)

    @staticmethod
    def signature(entry, hook=False):
        return (entry.get("frame_id"), entry.get("document_id"),
                urldefrag(entry.get("url", ""))[0], entry.get("method", "").upper(),
                entry.get("body_base64" if hook else "request_body_base64"))

    def initiator(self, entry):
        signature = self.signature(entry)
        candidates = [call for call in self.calls.values()
                      if call.get("body_ready") and self.signature(call, True) == signature
                      and call.get("type") == entry.get("resource_type")]
        peers = [req for req in self.by_id.values() if self.signature(req) == signature
                 and req.get("resource_type") == entry.get("resource_type")]
        # Unique payload + frame + document + method + exact URL only. Equal
        # duplicates cannot be ordered from browser events reliably (redirects,
        # scheduling, aborts). Keep all candidate IDs instead of a false stack.
        matched = len(candidates) == 1 and len(peers) == 1 and entry.get("document_id")
        call = candidates[0] if matched else None
        correlation = "exact" if matched else ("ambiguous" if candidates else "unmatched")
        return {
            "request_id": entry["id"], "url": entry["url"],
            "initiator_stack": call.get("stack") if call else None,
            "source": call["type"] if call else "unknown",
            "initiator_type": call["type"] if call else "unknown",
            "method": entry.get("method"),
            "request_headers": entry.get("request_headers"),
            "request_body": entry.get("request_post_data"),
            "request_body_base64": entry.get("request_body_base64"),
            "call_id": call["call_id"] if call else None,
            "runtime_request_id": entry.get("runtime_request_id"),
            "diagnostics": {"correlation": correlation,
                            "candidate_call_ids": [c["call_id"] for c in candidates],
                            "capture_gaps": len(self.gaps),
                            "hint": "Equal requests are ambiguous; no URL or FIFO fallback."},
        }

    def record_gap(self, frame, reason, **details):
        gap = {"frame_id": self.frame_id(frame),
               "document_id": self.documents.get(self.frame_id(frame)),
               "reason": reason, **details}
        key = json.dumps(gap, sort_keys=True)
        if key in self._gap_keys:
            return
        self.write("gaps", uuid.uuid4().hex, gap)
        self._gap_keys.add(key)
        self.gaps.append(gap)

    async def checkpoint(self):
        deadline = asyncio.get_running_loop().time() + self.checkpoint_timeout
        for context in tuple(self.contexts):
            for page in tuple(context.pages):
                if page.is_closed():
                    continue
                for frame in tuple(page.frames):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        self.record_gap(frame, "checkpoint_budget_exhausted")
                        continue
                    try:
                        snapshot = await asyncio.wait_for(frame.evaluate(
                            "mw:async () => { const s = window.__mcp_raw_network_state; "
                            "return s ? await s.drain(500) : {events: [], pending: 0}; }"
                        ), timeout=remaining)
                    except asyncio.TimeoutError:
                        self.record_gap(frame, "frame_checkpoint_timeout")
                        continue
                    except Exception as error:
                        self.record_gap(frame, "frame_checkpoint_unavailable", error=str(error))
                        continue
                    events = snapshot["events"]
                    for event in events:
                        self.receive({"frame": frame}, event)
                    if snapshot["pending"]:
                        self.record_gap(frame, "pending_hook_capture", pending=snapshot["pending"],
                                        observed_call_ids=[e["call_id"] for e in events if "call_id" in e])
        for entry in self.by_id.values():
            self.write("requests", entry["evidence_id"], entry)
            self.write("initiators", entry["evidence_id"], self.initiator(entry))
        if self.errors:
            raise RuntimeError("MCP network evidence persistence failed; navigation blocked")
