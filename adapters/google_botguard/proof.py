"""BotGuard proof 定位：从 batchexecute 调用记录中找出 VM proof 值。

proof 定义：f.req 递归解析后长度 > 500 的 "!" 前缀字符串。
多个候选全部列出并标记 ambiguous，不挑选。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

_RPID_RE = re.compile(r"rpcids=([A-Za-z0-9,_-]+)")
_MIN_PROOF_LENGTH = 500


@dataclass
class ProofToken:
    found: bool
    evidence: str  # observed | ambiguous | gap
    value: str | None = None
    rpcid: str | None = None
    request_artifact: str | None = None
    path: str = ""
    stack: str | None = None
    candidates: list[str] = field(default_factory=list)
    detail: str = ""

    @staticmethod
    def locate(session_dir: str | Path) -> "ProofToken":
        session = Path(session_dir)
        calls_dir = session / "raw" / "mcp-network" / "calls"
        hits: list[ProofToken] = []
        if calls_dir.exists():
            for call_file in sorted(calls_dir.glob("*/*.json")):
                try:
                    record = json.loads(call_file.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                url = record.get("url", "")
                if "accounts.google.com" not in url or "batchexecute" not in url:
                    continue
                body = record.get("body")
                if not body:
                    continue
                fields = dict(urllib.parse.parse_qsl(str(body)))
                f_req = fields.get("f.req")
                if not f_req:
                    continue
                rpcid_match = _RPID_RE.search(url)
                rpcid = rpcid_match.group(1) if rpcid_match else None
                try:
                    envelope = json.loads(f_req)
                except json.JSONDecodeError:
                    continue
                for path, value in _walk_proofs(envelope):
                    hits.append(ProofToken(
                        found=True, evidence="observed", value=value, rpcid=rpcid,
                        request_artifact=str(call_file), path=path,
                        stack=record.get("stack")))
        if not hits:
            return ProofToken(found=False, evidence="gap",
                              detail="no batchexecute call with a proof-shaped value")
        distinct = {hit.value for hit in hits}
        if len(distinct) > 1:
            return ProofToken(found=False, evidence="ambiguous",
                              candidates=sorted(distinct),
                              detail=f"{len(distinct)} distinct proof candidates")
        return hits[0]


def _walk_proofs(node, path: str = ""):
    """Yield (path, value) for every proof-shaped string in the envelope."""
    if isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_proofs(value, f"{path}[{index}]")
    elif isinstance(node, str):
        if node.startswith("!") and len(node) > _MIN_PROOF_LENGTH:
            yield path, node
        elif node.startswith("[") and len(node) > _MIN_PROOF_LENGTH:
            try:
                yield from _walk_proofs(json.loads(node), path)
            except json.JSONDecodeError:
                return
