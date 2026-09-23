"""Tests for raw reverse-analysis evidence and deterministic indexes."""

import hashlib
import json
import os

import pytest

from camoufox.reverse_evidence import EvidenceError, EvidenceStore
from camoufox.reverse_project import ReverseProject


def _store(tmp_path):
    session = ReverseProject.open(tmp_path / "p").create_session()
    return session, EvidenceStore(session)


def test_append_jsonl_preserves_raw_value(tmp_path):
    session, store = _store(tmp_path)

    store.append_jsonl("raw/network/0001.jsonl", {"body": "raw-secret-fixture"})

    assert "raw-secret-fixture" in (
        session.path / "raw/network/0001.jsonl"
    ).read_text()


def test_artifact_path_cannot_escape_session(tmp_path):
    session, store = _store(tmp_path)

    with pytest.raises(EvidenceError):
        store.append_bytes("../outside", b"bad")

    assert not (session.path.parent / "outside").exists()


def test_append_jsonl_writes_one_complete_json_object_per_line(tmp_path):
    session, store = _store(tmp_path)

    store.append_jsonl("raw/events.jsonl", {"sequence": 2, "text": "雪\nraw"})
    store.append_jsonl("raw/events.jsonl", {"sequence": 1, "text": "\ud800"})

    lines = (session.path / "raw/events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {"sequence": 2, "text": "雪\nraw"},
        {"sequence": 1, "text": "\ud800"},
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions required")
def test_raw_files_are_user_only(tmp_path):
    session, store = _store(tmp_path)

    store.append_bytes("raw/response.bin", b"raw")
    store.append_jsonl("raw/events.jsonl", {"ok": True})

    assert (session.path / "raw/response.bin").stat().st_mode & 0o777 == 0o600
    assert (session.path / "raw/events.jsonl").stat().st_mode & 0o777 == 0o600


def test_register_artifact_updates_registry_without_redacting_data(tmp_path):
    session, store = _store(tmp_path)
    path = "raw/scripts/app.js"
    payload = b"token=raw-secret-fixture\x00"
    store.append_bytes(path, payload)
    digest = hashlib.sha256(payload).hexdigest()

    store.register_artifact(path, digest, len(payload), "script")

    manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        {"path": path, "sha256": digest, "size": len(payload), "kind": "script"}
    ]
    assert "raw-secret-fixture" in (session.path / path).read_bytes().decode()


def test_rebuild_index_is_deterministic_and_does_not_mutate_raw_files(tmp_path):
    session, store = _store(tmp_path)
    first = b"first"
    second = b"second"
    store.append_bytes("raw/z.bin", second)
    store.append_jsonl("raw/a.jsonl", {"sequence": 2})
    store.append_bytes("raw/a.bin", first)
    store.register_artifact(
        "raw/z.bin", hashlib.sha256(second).hexdigest(), len(second), "response"
    )
    store.register_artifact(
        "raw/a.bin", hashlib.sha256(first).hexdigest(), len(first), "screenshot"
    )

    raw_before = {
        str(path.relative_to(session.path)): path.read_bytes()
        for path in session.path.glob("raw/**/*")
        if path.is_file()
    }
    first_index = store.rebuild_index()
    first_payload = first_index.read_bytes()
    second_index = store.rebuild_index()

    assert second_index == first_index
    assert first_index.read_bytes() == first_payload
    assert {
        str(path.relative_to(session.path)): path.read_bytes()
        for path in session.path.glob("raw/**/*")
        if path.is_file()
    } == raw_before

    index = json.loads(first_payload)
    assert index["event_loss"] == 0
    assert [item["path"] for item in index["artifacts"]] == [
        "raw/a.bin",
        "raw/a.jsonl",
        "raw/z.bin",
    ]
    assert index["artifacts"][0]["kind"] == "screenshot"
    assert index["files"]["raw/a.bin"]["bytes"] == len(first)
    assert index["files"]["raw/a.jsonl"]["event_count"] == 1


def test_rebuild_index_can_read_incomplete_session_and_reports_event_loss(tmp_path):
    session, store = _store(tmp_path)
    store.append_jsonl("raw/events.jsonl", {"sequence": 1})
    store.register_artifact(
        "raw/events.jsonl",
        hashlib.sha256((session.path / "raw/events.jsonl").read_bytes()).hexdigest(),
        (session.path / "raw/events.jsonl").stat().st_size,
        "network",
    )
    session.mark_incomplete()

    index_path = EvidenceStore(session).rebuild_index()

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["session_id"] == session.session_id
    assert index["event_loss"] == 0
    assert index["files"]["raw/events.jsonl"]["event_count"] == 1


def test_register_artifact_rejects_completed_session_without_writing_registry(tmp_path):
    session, store = _store(tmp_path)
    session.close()

    with pytest.raises(EvidenceError):
        store.register_artifact("raw/late.bin", "0" * 64, 0, "response")

    assert not (session.path / "raw/artifacts.jsonl").exists()


def test_index_sorting_is_independent_of_raw_file_creation_order(tmp_path):
    project = ReverseProject.open(tmp_path / "p")
    first_session = project.create_session()
    second_session = project.create_session()
    first_store = EvidenceStore(first_session)
    second_store = EvidenceStore(second_session)
    values = {"raw/b.bin": b"b", "raw/a.bin": b"a"}

    for path, value in values.items():
        first_store.append_bytes(path, value)
        first_store.register_artifact(path, hashlib.sha256(value).hexdigest(), len(value), "raw")
    for path, value in reversed(list(values.items())):
        second_store.append_bytes(path, value)
        second_store.register_artifact(path, hashlib.sha256(value).hexdigest(), len(value), "raw")

    first_index = json.loads(first_store.rebuild_index().read_text(encoding="utf-8"))
    second_index = json.loads(second_store.rebuild_index().read_text(encoding="utf-8"))
    first_index.pop("session_id")
    second_index.pop("session_id")

    assert first_index == second_index
