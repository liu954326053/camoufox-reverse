"""Pinned browser and PropertyTracer compatibility contract."""

from __future__ import annotations

UPSTREAM_VERSION = "152.0.4"
UPSTREAM_RELEASE = "beta.30"
UPSTREAM_VERSION_STRING = f"{UPSTREAM_VERSION}-{UPSTREAM_RELEASE}"
REVERSE_RELEASE = "reverse.9"
BROWSER_REPOSITORY = "whitenightshadow"
BROWSER_SELECTOR = f"{BROWSER_REPOSITORY}/{UPSTREAM_VERSION_STRING}-{REVERSE_RELEASE}"

PROPERTY_TRACE_PROTOCOL = 1
PROPERTY_TRACE_HOOKS = 77
PROPERTY_TRACE_STATUS_FIELDS = (
    "state",
    "session_id",
    "events",
    "dropped",
    "detail",
)
PROPERTY_TRACE_METADATA_ARTIFACT = "traces/*.meta.json"
PROPERTY_TRACE_FEATURES = (
    "async_buffered_io",
    "event_kind",
    "native_site",
    "wall_time_us",
    "sequence",
    "exclusive_session_files",
    "control_ack",
    "loss_status",
    "durable_loss_metadata",
    "utf8_paths",
    "process_scope",
    "script_exec_events",
)
REQUIRED_TRACE_FEATURES = frozenset(PROPERTY_TRACE_FEATURES)


def capability_contract() -> dict[str, object]:
    """Return the serializable capability contract shipped in browser archives."""
    return {
        "schema": 1,
        "distribution": "WhiteNightShadow/camoufox-reverse",
        "upstream_version": UPSTREAM_VERSION_STRING,
        "browser_selector": BROWSER_SELECTOR,
        "reverse_release": REVERSE_RELEASE,
        "property_trace": True,
        "property_trace_protocol": PROPERTY_TRACE_PROTOCOL,
        "property_trace_hooks": PROPERTY_TRACE_HOOKS,
        "property_trace_status_fields": list(PROPERTY_TRACE_STATUS_FIELDS),
        "property_trace_metadata_artifact": PROPERTY_TRACE_METADATA_ARTIFACT,
        "property_trace_features": list(PROPERTY_TRACE_FEATURES),
    }
