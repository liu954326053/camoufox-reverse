"""Contract fixtures and validation for the external reverse-browser MCP adapter."""

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "reverse-browser-mcp-contract.md"
SESSION_DIR = Path("/absolute/project/.reverse-browser/sessions/session-123")


LAUNCH_REQUEST = {
    "project_dir": "/absolute/project",
    "proxy": "http://127.0.0.1:7890",
    "trace_profile": "targeted",
}

LAUNCH_RESPONSE = {
    "status": "launched",
    "session_id": "session-123",
    "session_dir": str(SESSION_DIR),
    "browser_version": "152.0",
    "artifacts": {
        "manifest": str(SESSION_DIR / "manifest.json"),
        "trace_dir": str(SESSION_DIR / "trace"),
    },
}


def validate_response(response, *, session_dir):
    """Validate the deliberately small JSON envelope exposed to MCP callers."""
    assert set(response) <= {
        "status",
        "session_id",
        "session_dir",
        "browser_version",
        "count",
        "artifacts",
        "requests",
        "scripts",
        "page",
        "trace",
        "error",
    }
    assert response.get("error") is None
    for key in ("session_dir",):
        if key in response:
            assert Path(response[key]).is_relative_to(session_dir)
    for value in response.get("artifacts", {}).values():
        assert Path(value).is_relative_to(session_dir)


def test_launch_fixture_uses_required_project_and_trace_profile():
    assert LAUNCH_REQUEST == {
        "project_dir": "/absolute/project",
        "proxy": "http://127.0.0.1:7890",
        "trace_profile": "targeted",
    }
    assert "project_dir" in LAUNCH_REQUEST
    assert LAUNCH_REQUEST["trace_profile"] in {"overview", "targeted", "deep"}


def test_launch_response_contains_only_explicit_session_artifacts():
    validate_response(LAUNCH_RESPONSE, session_dir=SESSION_DIR)
    assert LAUNCH_RESPONSE["session_id"]
    assert set(LAUNCH_RESPONSE["artifacts"]) == {"manifest", "trace_dir"}
    assert all("tmp" not in path for path in LAUNCH_RESPONSE["artifacts"].values())


@pytest.mark.parametrize(
    "response",
    [
        {"status": "ok", "count": 2, "requests": []},
        {"status": "ok", "scripts": [], "count": 0},
        {"status": "ok", "trace": {"total_events": 3, "artifact": str(SESSION_DIR / "trace.jsonl")}},
    ],
)
def test_query_responses_are_machine_readable_and_path_scoped(response):
    validate_response(response, session_dir=SESSION_DIR)
    json.dumps(response)


def test_response_rejects_implicit_temporary_artifact_paths():
    response = {"status": "ok", "artifacts": {"capture": "/tmp/capture.json"}}

    with pytest.raises(AssertionError):
        validate_response(response, session_dir=SESSION_DIR)


def test_contract_document_declares_methods_and_path_rules():
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    for method in (
        "launch_browser",
        "get_page_info",
        "network_capture",
        "list_network_requests",
        "scripts",
        "trace_property_access",
        "export_state",
        "close_browser",
    ):
        assert method in text
    assert "project_dir" in text
    assert "current session directory" in text
