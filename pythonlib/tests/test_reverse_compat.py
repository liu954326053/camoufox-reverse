"""Tests for the pinned Camoufox Reverse build contract."""

import json
from pathlib import Path

from camoufox.reverse_compat import (
    BROWSER_SELECTOR,
    PROPERTY_TRACE_HOOKS,
    PROPERTY_TRACE_METADATA_ARTIFACT,
    PROPERTY_TRACE_PROTOCOL,
    PROPERTY_TRACE_STATUS_FIELDS,
    REQUIRED_TRACE_FEATURES,
    REVERSE_RELEASE,
    UPSTREAM_VERSION_STRING,
    capability_contract,
)


def test_pinned_browser_selector_is_the_updated_side_by_side_build():
    assert UPSTREAM_VERSION_STRING == "152.0.4-beta.30"
    assert REVERSE_RELEASE == "reverse.9"
    assert BROWSER_SELECTOR == "whitenightshadow/152.0.4-beta.30-reverse.9"


def test_source_capability_marker_matches_runtime_contract():
    path = Path(__file__).parents[2] / "settings" / "camoufox-reverse-capabilities.json"
    capabilities = json.loads(path.read_text(encoding="utf-8"))
    expected = capability_contract()

    assert capabilities["browser_selector"] == BROWSER_SELECTOR
    assert capabilities["reverse_release"] == REVERSE_RELEASE
    assert capabilities["property_trace_protocol"] == PROPERTY_TRACE_PROTOCOL
    assert capabilities["property_trace_hooks"] == PROPERTY_TRACE_HOOKS
    assert capabilities["property_trace_status_fields"] == list(
        PROPERTY_TRACE_STATUS_FIELDS
    )
    assert capabilities["property_trace_metadata_artifact"] == PROPERTY_TRACE_METADATA_ARTIFACT
    assert set(capabilities["property_trace_features"]) == REQUIRED_TRACE_FEATURES
    assert capabilities["upstream_version"] == expected["upstream_version"]
