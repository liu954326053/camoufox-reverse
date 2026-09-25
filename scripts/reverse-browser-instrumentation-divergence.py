#!/usr/bin/env python3
"""插桩分叉检测（第十阶段切片 E2，能力模型 P2 闭环）。

同一目标跑两会话：A 不装 vm_loop 插桩（仅被动网络捕获），B 全量插桩
（探针同款安装）。比较两侧可观测行为——请求 (method, host+path) 计数
多重集、响应状态分布、加密参数签名在位率、最终页面 URL——判定插桩
是否改变目标行为（强反调试目标如 Kasada 的核心验收）。

裁决（诚实口径）：
- aligned：归一化请求多重集一致且状态分布一致；
- minor：请求数差 ≤10%（负载均衡/AB 实验级波动），无新增 4xx/5xx，
  加密参数签名两侧都在位；
- diverged：其余情形，附首个差异证据；
- gap：任一侧会话失败，不伪造对比。

第十二阶段口径修正（phase12 实测 datadome 案例暴露）：
1. 请求 key 归一化：反爬请求 path/query 常含一次性 token
   （/challenge/<随机 hex>、?t=<时间戳>&token=<随机>），两侧天然不同，
   旧口径直接比 (method, host+path) 原文会虚增差异、把 minor 误判 diverged。
   现按「method + host + 归一化 path + query 键名多重集」比较，
   同时保留 raw（未归一化）计数对照，归一化吞掉的真实差异有据可查。
2. 加密参数在位率：旧口径只扫 URL，POST body 里的加密参数
   （DataDome sensor_data、瑞数表单字段）完全扫不到，两侧 0→0 失去意义。
   现分列 url_hits / body_hits，无 body 字段的请求单独计数说明。

只写 <project>/validation/<id>/instrumentation-divergence.json。
"""

import argparse
import json
import os
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "pythonlib"))
sys.path.insert(0, str(ROOT))
from camoufox_reverse_mcp_client import MCPStdioClient
from camoufox.reverse_compat import BROWSER_SELECTOR

# 与探针一致的加密参数签名清单（两侧在位率对比用）
ENCRYPTED_PARAM_HINTS = (
    "x-bogus", "xbogus", "mstoken", "_signature", "signature",
    "ttwid", "__ac_signature", "__ac_nonce", "x-tt-", "x-ss-",
    "sec-ch-", "x-kpsdk", "reese84", "sensor_data", "abck",
)


def unpack(reply):
    values = [json.loads(item["text"]) for item in reply.get("content", [])
              if item.get("type") == "text"]
    value = values[0] if len(values) == 1 else values
    if reply.get("isError") or isinstance(value, dict) and value.get("error"):
        raise RuntimeError(
            f"MCP tool failed: {json.dumps(value, ensure_ascii=False)[:400]}")
    return value


def run_session(args, instrumented: bool) -> dict:
    """跑一个会话，返回可观测行为快照。instrumented=False 不装 vm_loop。"""
    project = args.project_dir.resolve()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "pythonlib"), str(ROOT / "integrations/camoufox-reverse-mcp/src"),
         environment.get("PYTHONPATH", "")])
    server_args = ["-m", "camoufox_reverse_mcp", "--project-dir", str(project),
                   "--headless", "--os", "macos", "--locale", "en-US"]
    if args.proxy:
        server_args += ["--proxy", args.proxy]
    out = {"instrumented": instrumented}
    with MCPStdioClient(args.python, args=server_args,
                        env=environment, timeout=120) as client:
        client.initialize()

        def call(name, **arguments):
            return unpack(client.call_tool(name, arguments))

        launch_kwargs = dict(project_dir=str(project),
                             browser_version=args.browser_version,
                             headless=True, os_type="macos", locale="en-US",
                             enable_trace=False, capture_profile="raw")
        if args.proxy:
            launch_kwargs["proxy"] = args.proxy
        call("launch_browser", **launch_kwargs)
        call("network_capture", action="start", capture_body=False)
        if instrumented:
            install_kwargs = dict(url_pattern="**", tick_times=True,
                                  max_states_per_loop=200000)
            if args.code_patches:
                raw = args.code_patches
                if raw.startswith("@"):
                    raw = Path(raw[1:]).read_text()
                install_kwargs["code_patches"] = json.loads(raw)
            call("vm_loop_trace", action="install", **install_kwargs)
        try:
            call("navigate", url=args.target_url, wait_until="domcontentloaded",
                 clear_network_capture=False)
        except Exception as error:
            out["navigate_warning"] = str(error)[:200]
        waited = 0.0
        while waited < args.wait_seconds:
            step = min(2.0, args.wait_seconds - waited)
            try:
                call("evaluate_js",
                     expression=f"new Promise(r => setTimeout(r, {int(step * 1000)}))",
                     await_promise=True)
            except Exception:
                pass
            waited += step
        if args.click_expression:
            try:
                call("evaluate_js", expression=args.click_expression)
            except Exception as error:
                out["click_error"] = str(error)[:200]
            waited = 0.0
            while waited < args.post_click_wait_seconds:
                step = min(2.0, args.post_click_wait_seconds - waited)
                try:
                    call("evaluate_js",
                         expression=f"new Promise(r => setTimeout(r, {int(step * 1000)}))",
                         await_promise=True)
                except Exception:
                    pass
                waited += step
        requests = call("list_network_requests")
        if isinstance(requests, dict):
            requests = requests.get("result", [requests])
        out["requests"] = requests
        try:
            info = call("get_page_info")
            out["page"] = {k: info.get(k) for k in ("url", "title")}
        except Exception:
            pass
        if instrumented:
            try:
                out["route_stats"] = call("vm_loop_trace", action="stop")
            except Exception:
                pass
        call("close_browser")
    return out


def _request_key(req: dict) -> str:
    """原始 key（raw 口径，保留作对照）：method + host + path，query 不参与。"""
    url = req.get("url", "")
    try:
        parts = urlsplit(url)
        return f"{req.get('method', 'GET')} {parts.netloc}{parts.path}"
    except Exception:
        return f"{req.get('method', 'GET')} {url}"


# 第十二阶段：一次性 token 归一化规则。
# 反爬请求（datadome /challenge/<hex>、reese84、瑞数等）的 path 段常是
# 每次会话新生成的长随机串；两侧会话天然不同，直接比较会虚增差异。
# 只归一化「整段 ≥16 位十六进制」或「UUID 形态」的 path 段——这两类形态
# 几乎不可能是稳定的真实资源路径，误吞真实差异的风险低；
# 短段、含字母表外字符的段（如 .js 文件名）原样保留，保住真实区分度。
_HEX_SEGMENT_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
_UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def normalize_path(path: str) -> str:
    """把 path 里的一次性 token 段归一为占位符 <token>，其余段原样保留。"""
    segments = path.split("/")
    normalized = [
        "<token>" if (_HEX_SEGMENT_RE.match(seg) or _UUID_SEGMENT_RE.match(seg))
        else seg
        for seg in segments
    ]
    return "/".join(normalized)


# 第十三阶段：host 维度的一次性 token 归一化。
# 反爬/验证码服务常把每会话 token 编进子域标签（hCaptcha 的
# <12 位 hex>.w.hcaptcha.com、部分 CDN 的 <随机串>.edge.<domain>），
# 两侧会话天然不同，旧口径只归一 path 段，host 里的 token 会虚增差异。
# 判据与 path 段同款保守风格：纯 hex ≥10 位（子域标签的 hex token 常见
# 10~12 位，比 path 段阈值放宽，因 DNS 标签本身更短）或 UUID 形态的标签
# 才归一；短标签、含字母表外字符的标签（api./static./www./newassets.
# 这类功能子域）原样保留。netloc 可能带端口（:443 仅 3 位，不匹配阈值），
# 不受影响。
_HEX_LABEL_RE = re.compile(r"^[0-9a-fA-F]{10,}$")


def normalize_host(netloc: str) -> str:
    """把 host 里的一次性 token 标签归一为占位符 <token>，其余标签原样保留。"""
    labels = netloc.split(".")
    normalized = [
        "<token>" if (_HEX_LABEL_RE.match(label) or _UUID_SEGMENT_RE.match(label))
        else label
        for label in labels
    ]
    return ".".join(normalized)


def _query_key_multiset(query: str) -> str:
    """query 只保留键名多重集（排序后拼接），值全部丢弃。

    理由：query 值高频出现时间戳/一次性 token（?t=...&token=...），
    参与比较必产生虚差；但键名集合本身是真实区分度
    （?a=1 与 ?a=1&b=2 是不同请求形态），故保留键名多重集而非整体丢弃。
    """
    try:
        keys = sorted(k for k, _ in parse_qsl(query, keep_blank_values=True))
    except Exception:
        keys = []
    return "&".join(keys)


def normalize_request_key(req: dict) -> str:
    """归一化 key：method + 归一化 host + 归一化 path + query 键名多重集。

    保留的真实区分度：请求方法、主机（去掉一次性 token 标签后）、
    路径结构（去掉一次性 token 段后）、query 键名多重集。任一不同即视为真实差异。
    """
    url = req.get("url", "")
    try:
        parts = urlsplit(url)
        query_keys = _query_key_multiset(parts.query)
        base = f"{req.get('method', 'GET')} {normalize_host(parts.netloc)}{normalize_path(parts.path)}"
        return f"{base}?{query_keys}" if query_keys else base
    except Exception:
        return f"{req.get('method', 'GET')} {url}"


def _post_body(req: dict):
    """取请求 POST body。捕获记录里常见字段名 postData / post_data 都认。

    返回 None 表示该请求没有 body 字段（调用方跳过并计数说明）。
    """
    body = req.get("postData", req.get("post_data"))
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=False)
    return str(body)


# 第十四阶段：瑞数 v5/v6 动态参数签名检测。
# 瑞数的签名参数名每个部署/每轮换名（sso.cnipa.gov.cn 实测为 qco9Ha2n、
# cookie BOJRPc7LzNOPP），无法进静态 hints 清单；但值形态稳定——
# 三段点分 base64url（<seg>.<seg>.<seg>，段长 ≥8），挂 query 或 cookie。
# 用值形态正则兜底：参数名任意（≤32 字符），值为三段点分签名即命中。
# 已知误中面：JWT（header.payload.signature）同形态——本计数是
# 「加密参数在位率」启发式，命中 JWT 同样说明签名类参数在传输，可接受。
_DOTTED_SIG_RE = re.compile(
    r"(?:^|[?&;\s])[^=\s&;]{1,32}=[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}")


def _encrypted_hits(requests: list) -> dict:
    """加密参数在位率：URL 与 POST body 分列计数（第十二阶段）。

    旧口径只扫 URL 小写串，POST body 里的加密参数（DataDome 传感器
    payload、瑞数表单字段）完全扫不到，两侧 0→0 失去对比意义。
    返回 {"url_hits", "body_hits", "no_body"}：no_body 是没有 body 字段、
    被跳过 body 扫描的请求数，用于说明覆盖范围。
    """
    url_hits = body_hits = no_body = 0
    for req in requests:
        url = (req.get("url") or "").lower()
        if (any(h in url for h in ENCRYPTED_PARAM_HINTS)
                or _DOTTED_SIG_RE.search(req.get("url") or "")):
            url_hits += 1
        body = _post_body(req)
        if body is None:
            no_body += 1
        elif (any(h in body.lower() for h in ENCRYPTED_PARAM_HINTS)
              or _DOTTED_SIG_RE.search(body)):
            body_hits += 1
    return {"url_hits": url_hits, "body_hits": body_hits, "no_body": no_body}


def _encrypted_total(hits: dict) -> int:
    return hits["url_hits"] + hits["body_hits"]


def compare(passive: dict, instrumented: dict) -> dict:
    """对比两侧可观测行为，输出分叉裁决与证据。

    裁决以归一化 key 为准；raw key 计数同步输出作对照（诚实纪律：
    归一化可能吞掉真实差异，两组数字都给，人工可查）。
    """
    pa_reqs = passive.get("requests") or []
    in_reqs = instrumented.get("requests") or []
    # raw 口径：旧版 (method, host+path) 原文比较，仅作对照输出
    pa_raw = Counter(_request_key(r) for r in pa_reqs)
    in_raw = Counter(_request_key(r) for r in in_reqs)
    raw_only_passive = pa_raw - in_raw
    raw_only_instrumented = in_raw - pa_raw
    # 归一化口径：裁决依据
    pa_keys = Counter(normalize_request_key(r) for r in pa_reqs)
    in_keys = Counter(normalize_request_key(r) for r in in_reqs)
    only_passive = pa_keys - in_keys
    only_instrumented = in_keys - pa_keys
    # 仅一次性 token 不同的请求：raw 有差异、归一化后消失的部分
    token_only_count = (sum(raw_only_passive.values()) - sum(only_passive.values())
                        + sum(raw_only_instrumented.values())
                        - sum(only_instrumented.values()))
    pa_status = Counter(str(r.get("status")) for r in pa_reqs)
    in_status = Counter(str(r.get("status")) for r in in_reqs)
    pa_err = sum(v for k, v in pa_status.items()
                 if k.startswith("4") or k.startswith("5"))
    in_err = sum(v for k, v in in_status.items()
                 if k.startswith("4") or k.startswith("5"))
    pa_enc = _encrypted_hits(pa_reqs)
    in_enc = _encrypted_hits(in_reqs)
    pa_enc_total = _encrypted_total(pa_enc)
    in_enc_total = _encrypted_total(in_enc)
    total = max(len(pa_reqs), 1)
    diff_ratio = abs(len(in_reqs) - len(pa_reqs)) / total

    report = {
        "schema": 2,
        "requests": {"passive": len(pa_reqs), "instrumented": len(in_reqs),
                     "diff_ratio": round(diff_ratio, 4)},
        "request_key_diff": {
            # normalized：裁决依据的差异
            "normalized": {
                "only_passive": dict(only_passive.most_common(10)),
                "only_instrumented": dict(only_instrumented.most_common(10))},
            # raw：未归一化对照，归一化是否吞掉真实差异看这里
            "raw": {
                "only_passive": dict(raw_only_passive.most_common(10)),
                "only_instrumented": dict(raw_only_instrumented.most_common(10))},
            # raw 有差异但归一化后消失 = 仅一次性 token 不同
            "token_only_count": token_only_count},
        "status": {"passive": dict(pa_status),
                   "instrumented": dict(in_status)},
        "encrypted_param_requests": {"passive": pa_enc,
                                     "instrumented": in_enc},
        "page": {"passive": passive.get("page"),
                 "instrumented": instrumented.get("page")},
    }
    if not pa_reqs or not in_reqs:
        report["verdict"] = "gap"
        report["detail"] = "任一侧无请求记录，不伪造对比"
    elif (not only_passive and not only_instrumented
          and pa_status == in_status):
        report["verdict"] = "aligned"
        report["detail"] = ("归一化请求多重集与状态分布完全一致"
                            + (f"（另 %d 条仅一次性 token 不同，已归一）"
                               % token_only_count if token_only_count else ""))
    elif (diff_ratio <= 0.10 and in_err <= pa_err
          and (pa_enc_total == 0 or in_enc_total > 0)):
        report["verdict"] = "minor"
        report["detail"] = ("请求数差 %.1f%%（负载波动级），无新增错误状态，"
                            "加密参数签名两侧在位（url %d→%d / body %d→%d）；"
                            "归一化后真实差异 %d 条，仅 token 差异 %d 条"
                            % (diff_ratio * 100,
                               pa_enc["url_hits"], in_enc["url_hits"],
                               pa_enc["body_hits"], in_enc["body_hits"],
                               sum(only_passive.values())
                               + sum(only_instrumented.values()),
                               token_only_count))
    else:
        report["verdict"] = "diverged"
        report["detail"] = ("请求数差 %.1f%%，被动侧错误 %d / 插桩侧错误 %d，"
                            "加密参数 url %d→%d / body %d→%d；"
                            "归一化后仍不同 %d 条（真实差异），仅 token 不同 %d 条"
                            % (diff_ratio * 100, pa_err, in_err,
                               pa_enc["url_hits"], in_enc["url_hits"],
                               pa_enc["body_hits"], in_enc["body_hits"],
                               sum(only_passive.values())
                               + sum(only_instrumented.values()),
                               token_only_count))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--browser-version", default=BROWSER_SELECTOR)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--wait-seconds", type=int, default=12)
    parser.add_argument("--click-expression", default="")
    parser.add_argument("--post-click-wait-seconds", type=int, default=8)
    parser.add_argument("--code-patches", default="",
                        help="插桩侧的适配器锚点补丁（JSON 或 @文件）")
    args = parser.parse_args()

    args.project_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_dir = args.project_dir / "validation" / uuid.uuid4().hex
    report_dir.mkdir(parents=True, mode=0o700)

    passive = run_session(args, instrumented=False)
    instrumented = run_session(args, instrumented=True)
    report = compare(passive, instrumented)
    report["target_url"] = args.target_url
    report["sessions"] = {"passive": {k: v for k, v in passive.items()
                                      if k != "requests"},
                          "instrumented": {k: v for k, v in instrumented.items()
                                           if k != "requests"}}
    out = report_dir / "instrumentation-divergence.json"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": report["verdict"],
                      "detail": report["detail"],
                      "report": str(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
