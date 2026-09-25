"""Session-scoped MCP environment self-check."""
from __future__ import annotations

import importlib
import json
import uuid
from typing import Any

from ..server import mcp, browser_manager


@mcp.tool()
async def check_environment() -> dict:
    """One-stop self-check of MCP environment, dependencies, and browser state.

    v1.0.0: session-related checks removed (session mechanism removed).
    Checks MCP version, critical dependencies (esprima, playwright),
    browser state (residuals, captures).

    Returns:
        dict with sections: mcp, deps, browser, overall_ok, recommendations.
    """
    recommendations: list[str] = []

    # MCP version
    try:
        mod = importlib.import_module("camoufox_reverse_mcp")
        version = getattr(mod, "__version__", "unknown")
        parts = tuple(int(x) for x in version.split(".") if x.isdigit())
        version_ok = parts >= (1, 0, 0)
    except Exception:
        version = "unknown"
        version_ok = False
    if not version_ok:
        recommendations.append(f"MCP version is {version}, need >= 1.0.0.")

    # Dependencies
    deps: dict[str, dict] = {}
    for dep in ("esprima", "playwright"):
        try:
            m = importlib.import_module(dep)
            deps[dep] = {"installed": True, "version": getattr(m, "__version__", "unknown"), "ok": True}
        except ImportError:
            deps[dep] = {"installed": False, "version": None, "ok": False}

    # Browser state
    browser_state: dict[str, Any] = {"running": False}
    try:
        if browser_manager.browser is not None:
            browser_state["running"] = True
            ctx = browser_manager.contexts.get("default")
            pages = ctx.pages if ctx else []
            browser_state["page_count"] = len(pages)
            browser_state["persistent_scripts_count"] = len(browser_manager._persistent_scripts)
            browser_state["active_captures"] = browser_manager._capturing
            browser_state["captured_requests_count"] = len(browser_manager._network_requests)
            has_residuals = (
                browser_state["persistent_scripts_count"] > 0
                or browser_state["captured_requests_count"] > 0
            )
            browser_state["has_residuals"] = has_residuals
            if has_residuals:
                recommendations.append("Browser has residual state. Consider reset_browser_state().")
    except Exception as e:
        browser_state["error"] = str(e)

    overall_ok = version_ok and all(d["ok"] for d in deps.values() if d.get("installed"))

    session = browser_manager.session
    session_metadata = browser_manager.session_metadata()

    # camoufox-reverse custom browser detection. The adapter never consults
    # the process-wide cache; trace state belongs to the active runtime.
    from ..property_trace import active_trace_dir, list_session_files
    custom_browser: dict[str, Any] = {"installed": False}
    trace_root = active_trace_dir()
    if session is None or trace_root is None:
        custom_browser = {
            "installed": False,
            "trace_active": False,
            "reason": "no_active_session",
        }
    else:
        control_dir = trace_root / "control"
        ctrl_files = list(control_dir.glob("control-*.cmd"))
        trace_files = list_session_files()
        custom_browser = {
            "installed": bool(ctrl_files),
            "trace_active": bool(ctrl_files),
            "control_files": len(ctrl_files),
            "trace_files": len(trace_files),
            "trace_dir": str(trace_root.resolve()),
        }

    result = {
        "status": "ok",
        "mcp": {"version": version, "version_ok": version_ok},
        "deps": deps,
        "browser": browser_state,
        "camoufox_reverse": custom_browser,
        "overall_ok": overall_ok,
        "recommendations": recommendations,
    }
    if session is None:
        return result

    result.update(
        {
            "session_id": session_metadata["session_id"],
            "session_dir": session_metadata["session_dir"],
        }
    )
    report = json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
    artifact = browser_manager.write_artifact(
        None,
        report,
        "environment-status",
        default=f"raw/status/environment-{uuid.uuid4().hex}.json",
    )
    artifacts = dict(session_metadata.get("artifacts", {}))
    artifacts["environment"] = str(artifact.resolve())
    result["artifacts"] = artifacts
    return result
