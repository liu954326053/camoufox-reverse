"""Tests for project-scoped reverse browser launch options."""

import json

import pytest

from camoufox import launch_options as ordinary_launch_options
from camoufox import reverse_launch_options


def test_reverse_launch_requires_project_dir():
    with pytest.raises(TypeError):
        reverse_launch_options()


def test_reverse_launch_points_property_trace_inside_session(tmp_path, monkeypatch):
    monkeypatch.setattr("camoufox.reverse_launch.launch_options", lambda **kwargs: kwargs)

    options, session = reverse_launch_options(
        project_dir=tmp_path / "p",
        proxy="http://127.0.0.1:7890",
        trace_profile="targeted",
    )

    assert options["config"]["propertyTrace"]["logDir"] == str(session.trace_dir)
    assert options["config"]["propertyTrace"]["enabled"] is True
    assert session.trace_dir == session.path / "trace"
    assert session.trace_dir.is_dir()


def test_reverse_launch_normalizes_proxy_and_browser_selector(tmp_path, monkeypatch):
    captured = {}

    def fake_launch_options(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr("camoufox.reverse_launch.launch_options", fake_launch_options)

    reverse_launch_options(
        project_dir=tmp_path / "p",
        proxy="http://alice:secret@example.test:8080",
        browser_version="official/beta.20",
    )

    assert captured["proxy"] == {
        "server": "http://example.test:8080",
        "username": "alice",
        "password": "secret",
    }
    assert captured["browser"] == "official/beta.20"


def test_reverse_launch_preserves_caller_config_and_trace_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("camoufox.reverse_launch.launch_options", lambda **kwargs: kwargs)
    config = {
        "navigator.userAgent": "caller-value",
        "propertyTrace": {"objects": ["crypto"], "maxEventsPerSession": 7},
    }

    options, _ = reverse_launch_options(
        project_dir=tmp_path / "p",
        config=config,
        trace_profile="deep",
    )

    assert options["config"]["navigator.userAgent"] == "caller-value"
    assert options["config"]["propertyTrace"]["objects"] == ["crypto"]
    assert options["config"]["propertyTrace"]["maxEventsPerSession"] == 7
    assert options["config"]["propertyTrace"]["enabled"] is True


def test_reverse_launch_marks_session_running_after_launch_options(tmp_path, monkeypatch):
    monkeypatch.setattr("camoufox.reverse_launch.launch_options", lambda **kwargs: kwargs)

    _, session = reverse_launch_options(project_dir=tmp_path / "p")

    manifest = json.loads(session.manifest_path.read_text())
    assert manifest["status"] == "running"
    assert manifest["trace_profile"] == "overview"


def test_reverse_launch_rejects_unknown_trace_profile(tmp_path, monkeypatch):
    called = False

    def fail_if_called(**kwargs):
        nonlocal called
        called = True
        return kwargs

    monkeypatch.setattr("camoufox.reverse_launch.launch_options", fail_if_called)

    with pytest.raises(ValueError, match="trace_profile"):
        reverse_launch_options(project_dir=tmp_path / "p", trace_profile="invalid")

    assert called is False


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("launch failed"),
        ValueError("browser selector is invalid"),
    ],
)
def test_reverse_launch_marks_session_incomplete_on_initialization_failure(
    tmp_path, monkeypatch, failure
):
    def fail_launch_options(**kwargs):
        raise failure

    monkeypatch.setattr("camoufox.reverse_launch.launch_options", fail_launch_options)

    with pytest.raises(type(failure), match=str(failure)):
        reverse_launch_options(
            project_dir=tmp_path / "p",
            proxy="http://127.0.0.1:7890",
            browser_version="official/beta.20",
        )

    manifests = list((tmp_path / "p" / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "incomplete"


def test_reverse_launch_marks_session_incomplete_when_proxy_validation_fails(tmp_path):
    with pytest.raises(ValueError, match="proxy"):
        reverse_launch_options(project_dir=tmp_path / "p", proxy="not-a-url")

    manifests = list((tmp_path / "p" / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "incomplete"


def test_ordinary_launch_options_remains_exported():
    assert callable(ordinary_launch_options)
    assert ordinary_launch_options.__module__ == "camoufox.utils"
