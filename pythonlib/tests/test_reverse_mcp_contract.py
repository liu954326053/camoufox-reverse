"""Executable JSON contract checks for the external reverse-browser MCP adapter."""

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "reverse-browser-mcp-contract.md"
SUCCESS_STATUSES = {
    "already_closed",
    "cleared",
    "closed",
    "launched",
    "ok",
    "started",
    "stopped",
}
REQUIRED_TOP_LEVEL = {
    "status",
    "session_id",
    "session_dir",
}
SESSION_DIR = Path("/absolute/project/.reverse-browser/sessions/session-123")


LAUNCH_REQUEST = {
    "project_dir": "/absolute/project",
    "proxy": "http://127.0.0.1:7890",
    "trace_profile": "targeted",
    "capture_profile": "raw",
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

RAW_CAPTURE_RESPONSE = {
    "status": "started",
    "session_id": "session-123",
    "session_dir": str(SESSION_DIR),
    "capture_profile": "raw",
    "count": 0,
    "artifacts": {
        "capture": str(SESSION_DIR / "network" / "raw.jsonl"),
    },
}

TRACE_RESPONSE = {
    "status": "ok",
    "session_id": "session-123",
    "session_dir": str(SESSION_DIR),
    "trace": {
        "total_events": 3,
        "artifact": str(SESSION_DIR / "trace.jsonl"),
    },
}

ERROR_RESPONSE = {
    "status": "error",
    "error": {"code": "invalid_project_dir", "message": "project_dir is required"},
}

CLOSE_RESPONSE = {
    "status": "closed",
    "session_id": "session-123",
    "session_dir": str(SESSION_DIR),
}
SECOND_CLOSE_RESPONSE = {
    "status": "already_closed",
    "session_id": "session-123",
    "session_dir": str(SESSION_DIR),
}


def _assert_contained(value, *, session_dir):
    candidate = Path(value)
    assert candidate.is_absolute()
    assert candidate.resolve(strict=False).is_relative_to(
        session_dir.resolve(strict=False)
    )


def _assert_paths_contained(value, *, session_dir, key=""):
    """Recursively inspect explicit path/artifact fields in response JSON."""
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if child_key in {"artifact", "artifacts", "path", "save_path"}:
                if child_key == "artifacts":
                    assert isinstance(child_value, dict)
                    for artifact in child_value.values():
                        _assert_paths_contained(artifact, session_dir=session_dir, key="artifact")
                else:
                    _assert_paths_contained(child_value, session_dir=session_dir, key=child_key)
            else:
                _assert_paths_contained(child_value, session_dir=session_dir, key=child_key)
    elif isinstance(value, list):
        for item in value:
            _assert_paths_contained(item, session_dir=session_dir, key=key)
    elif key in {"artifact", "path", "save_path"}:
        _assert_contained(value, session_dir=session_dir)


def _assert_raw_capture_profiles(value):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if child_key == "capture_profile":
                assert child_value == "raw"
            _assert_raw_capture_profiles(child_value)
    elif isinstance(value, list):
        for item in value:
            _assert_raw_capture_profiles(item)


def validate_project_dir(request):
    """Validate launch input before any session is created."""
    assert "project_dir" in request
    value = request["project_dir"]
    assert isinstance(value, str) and value.strip()
    project_dir = Path(value)
    assert project_dir.is_absolute()
    assert project_dir.exists() and project_dir.is_dir()
    resolved = project_dir.resolve(strict=True)
    assert resolved == project_dir
    mode = resolved.stat().st_mode
    assert mode & (0o222)
    return resolved


def validate_launch_request(request):
    project_dir = validate_project_dir(request)
    assert request.get("capture_profile", "raw") == "raw"
    assert request.get("trace_profile", "overview") in {"overview", "targeted", "deep"}
    return project_dir


def validate_response(response, *, session_dir, required_fields=REQUIRED_TOP_LEVEL):
    """Validate the stable response envelope and resolved artifact containment."""
    allowed = {
        "status",
        "session_id",
        "session_dir",
        "browser_version",
        "capture_profile",
        "count",
        "artifacts",
        "requests",
        "scripts",
        "page",
        "trace",
        "error",
    }
    assert set(response) <= allowed
    assert "status" in response
    assert response["status"] in SUCCESS_STATUSES
    assert set(required_fields) <= set(response)
    assert response["session_id"]
    actual_session_dir = Path(response["session_dir"])
    expected_session_dir = session_dir.resolve(strict=False)
    assert actual_session_dir.is_absolute()
    assert actual_session_dir == actual_session_dir.resolve(strict=False)
    assert actual_session_dir == expected_session_dir
    assert "error" not in response
    _assert_raw_capture_profiles(response)
    _assert_paths_contained(response, session_dir=expected_session_dir)


def validate_error_response(response):
    assert response == {"status": "error", "error": response["error"]}
    assert isinstance(response["error"]["code"], str)
    assert response["error"]["code"]
    assert isinstance(response["error"]["message"], str)
    assert response["error"]["message"]
    assert "artifacts" not in response
    assert "session_dir" not in response


def validate_export_state_response(response, *, session_dir):
    validate_response(response, session_dir=session_dir)
    assert "artifacts" in response
    assert "state" in response["artifacts"]


def test_launch_fixture_requires_raw_capture_profile():
    assert LAUNCH_REQUEST["capture_profile"] == "raw"


def test_raw_capture_profile_is_default_and_only_supported_research_mode(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    request = {**LAUNCH_REQUEST, "project_dir": str(project)}
    assert validate_launch_request(request) == project
    with pytest.raises(AssertionError):
        validate_launch_request({**request, "capture_profile": "overview"})


def test_raw_capture_response_exposes_profile_status_and_artifact():
    validate_response(RAW_CAPTURE_RESPONSE, session_dir=SESSION_DIR)
    assert RAW_CAPTURE_RESPONSE["capture_profile"] == "raw"
    assert RAW_CAPTURE_RESPONSE["status"] == "started"
    assert RAW_CAPTURE_RESPONSE["artifacts"]["capture"].endswith("raw.jsonl")


@pytest.mark.parametrize(
    "response",
    [
        {**RAW_CAPTURE_RESPONSE, "capture_profile": "metadata_only"},
        {
            **RAW_CAPTURE_RESPONSE,
            "trace": {"capture_profile": "metadata_only"},
        },
    ],
)
def test_response_rejects_any_non_raw_capture_profile(response):
    with pytest.raises(AssertionError):
        validate_response(response, session_dir=SESSION_DIR)


def test_trace_response_checks_nested_artifact_paths():
    validate_response(TRACE_RESPONSE, session_dir=SESSION_DIR)
    json.dumps(TRACE_RESPONSE)


def test_project_dir_is_required_and_rejects_empty_relative_and_missing_values(tmp_path):
    valid = tmp_path / "project"
    valid.mkdir()
    assert validate_launch_request({"project_dir": str(valid)}) == valid
    for request in (
        {},
        {"project_dir": ""},
        {"project_dir": "   "},
        {"project_dir": "relative/project"},
        {"project_dir": str(tmp_path / "missing")},
    ):
        with pytest.raises(AssertionError):
            validate_launch_request(request)


def test_project_dir_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "project-link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AssertionError):
        validate_launch_request({"project_dir": str(link)})


def test_project_dir_requires_writable_directory(tmp_path):
    project = tmp_path / "read-only"
    project.mkdir()
    project.chmod(0o555)
    try:
        with pytest.raises(AssertionError):
            validate_launch_request({"project_dir": str(project)})
    finally:
        project.chmod(0o755)


@pytest.mark.parametrize(
    "response",
    [
        {"status": "ok", "session_id": "session-123", "session_dir": str(SESSION_DIR), "count": 2, "requests": []},
        {"status": "ok", "session_id": "session-123", "session_dir": str(SESSION_DIR), "scripts": [], "count": 0},
    ],
)
def test_query_responses_require_status_session_and_absolute_normalized_session_dir(response):
    validate_response(response, session_dir=SESSION_DIR)


def test_response_rejects_missing_required_fields_and_unknown_status():
    for response in (
        {"status": "ok", "session_dir": str(SESSION_DIR)},
        {"status": "accepted", "session_id": "session-123", "session_dir": str(SESSION_DIR)},
        {"status": "ok", "session_id": "session-123", "session_dir": "relative/session"},
    ):
        with pytest.raises(AssertionError):
            validate_response(response, session_dir=SESSION_DIR)


def test_response_rejects_traversal_and_symlink_escaping_nested_artifacts(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    escape_link = session_dir / "escape"
    escape_link.symlink_to(outside, target_is_directory=True)

    responses = [
        {"status": "ok", "session_id": "s", "session_dir": str(session_dir), "artifacts": {"x": str(session_dir / ".." / "outside.json")}},
        {"status": "ok", "session_id": "s", "session_dir": str(session_dir), "trace": {"artifact": str(escape_link / "trace.jsonl")}},
        {"status": "ok", "session_id": "s", "session_dir": str(session_dir), "artifacts": {"nested": {"path": str(session_dir / ".." / "outside.json")}}},
    ]
    for response in responses:
        with pytest.raises(AssertionError):
            validate_response(response, session_dir=session_dir)


def test_export_state_save_path_is_explicit_and_session_scoped(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    valid = {
        "status": "ok",
        "session_id": "s",
        "session_dir": str(session_dir),
        "artifacts": {"state": str(session_dir / "state.json")},
    }
    validate_export_state_response(valid, session_dir=session_dir)
    for save_path in (session_dir / ".." / "outside.json", Path("relative.json")):
        invalid = {**valid, "artifacts": {"state": str(save_path)}}
        with pytest.raises(AssertionError):
            validate_export_state_response(invalid, session_dir=session_dir)


def test_error_envelope_is_valid_and_has_no_artifacts():
    validate_error_response(ERROR_RESPONSE)
    with pytest.raises(AssertionError):
        validate_error_response({**ERROR_RESPONSE, "artifacts": {}})


@pytest.mark.parametrize(
    "field, value",
    [
        ("code", ""),
        ("code", 400),
        ("message", ""),
        ("message", {"detail": "invalid"}),
    ],
)
def test_error_envelope_rejects_empty_or_non_string_code_and_message(field, value):
    response = {
        "status": "error",
        "error": {**ERROR_RESPONSE["error"], field: value},
    }
    with pytest.raises(AssertionError):
        validate_error_response(response)


def test_close_and_second_close_have_explicit_statuses():
    validate_response(CLOSE_RESPONSE, session_dir=SESSION_DIR)
    validate_response(SECOND_CLOSE_RESPONSE, session_dir=SESSION_DIR)
    assert CLOSE_RESPONSE["status"] == "closed"
    assert SECOND_CLOSE_RESPONSE["status"] == "already_closed"


def test_contract_document_declares_executable_rules_and_methods():
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    for phrase in (
        "capture_profile",
        "raw",
        "current session directory",
        "resolve()",
        "symlink",
        '"status": "error"',
        "already_closed",
        "export_state",
    ):
        assert phrase in text
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
