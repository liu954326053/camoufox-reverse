"""BotGuard VM 程序提取：从 session raw 证据定位 VM 程序字节与程序 hash。

只读 raw/，找不到时返回 gap 结果，绝不猜测程序内容。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# AF_initDataCallback 数据块中紧邻 "botguard" 的长 base64 blob。
_BLOB_RE = re.compile(r'"([A-Za-z0-9+/=_-]{200,})"\s*,\s*"botguard"')

# 真实页面里 blob 本身是完整 base64（"/" 是合法字符，不能被 "//" 拆分）；
# 旧格式才在最后一个 "//" 之后接程序体。依次尝试，严格校验，拒绝静默丢字符。
_MIN_PROGRAM_SIZE = 1000


def _decode_program(blob: str) -> bytes | None:
    # 严格优先：先整体、再 // 尾部；urlsafe 变体不校验字符，只作兜底。
    strict = {"validate": True}
    candidates = [
        (blob, base64.b64decode, strict),
        (blob.rsplit("//", 1)[-1], base64.b64decode, strict),
        (blob, base64.urlsafe_b64decode, {}),
        (blob.rsplit("//", 1)[-1], base64.urlsafe_b64decode, {}),
    ]
    for payload, decoder, kwargs in candidates:
        padded = payload + "=" * (-len(payload) % 4)
        try:
            data = decoder(padded, **kwargs)
        except (binascii.Error, ValueError):
            continue
        if len(data) >= _MIN_PROGRAM_SIZE:
            return data
    return None


@dataclass
class BotGuardProgram:
    found: bool
    evidence: str  # observed | gap
    bytes: bytes = b""
    sha256: str | None = None
    source_artifact: str | None = None
    detail: str = ""
    candidates: list[str] = field(default_factory=list)

    @staticmethod
    def extract(session_dir: str | Path) -> "BotGuardProgram":
        session = Path(session_dir)
        blobs: list[tuple[Path, str]] = []
        network_dir = session / "raw" / "network"
        if network_dir.exists():
            for body_path in sorted(network_dir.glob("*/response.body")):
                try:
                    text = body_path.read_text(errors="replace")
                except OSError:
                    continue
                if "botguard" not in text:
                    continue
                for match in _BLOB_RE.finditer(text):
                    blobs.append((body_path, match.group(1)))
        decoded: dict[bytes, Path] = {}
        rejected = 0
        for path, blob in blobs:
            program_bytes = _decode_program(blob)
            if program_bytes is None:
                rejected += 1
                continue
            decoded.setdefault(program_bytes, path)
        if not decoded:
            return BotGuardProgram(
                found=False, evidence="gap",
                detail=f"no decodable botguard program blob "
                       f"({len(blobs)} candidates, {rejected} undecodable)")
        if len(decoded) > 1:
            program = BotGuardProgram(
                found=False, evidence="gap",
                detail=f"{len(decoded)} distinct programs; refusing to pick one")
            program.candidates = [hashlib.sha256(b).hexdigest() for b in decoded]
            return program
        program_bytes, source = next(iter(decoded.items()))
        return BotGuardProgram(
            found=True, evidence="observed", bytes=program_bytes,
            sha256=hashlib.sha256(program_bytes).hexdigest(),
            source_artifact=str(source))
