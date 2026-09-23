"""Tests for raw reverse-analysis evidence and deterministic indexes."""

import hashlib
import json
import os

import pytest

from camoufox import reverse_evidence
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


def test_complete_session_rejects_rebuild_and_preserves_existing_index(tmp_path):
    session, store = _store(tmp_path)
    store.append_bytes("raw/response.bin", b"before")
    index_path = store.rebuild_index()
    before = index_path.read_bytes()
    session.close()

    with pytest.raises(EvidenceError):
        EvidenceStore(session).rebuild_index()

    assert index_path.read_bytes() == before


def test_parent_symlink_replacement_between_mkdir_and_open_cannot_redirect_write(
    tmp_path, monkeypatch
):
    session, store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    nested = session.path / "raw/nested"
    original_open = reverse_evidence.os.open
    replaced = False

    def replace_before_open(path, *args, **kwargs):
        nonlocal replaced
        if (
            path == "nested"
            and kwargs.get("dir_fd") is not None
            and nested.exists()
            and not replaced
        ):
            replaced = True
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(reverse_evidence.os, "open", replace_before_open)

    with pytest.raises(EvidenceError):
        store.append_bytes("raw/nested/evidence.bin", b"must-stay-inside")

    assert not (outside / "evidence.bin").exists()


def test_registry_is_durable_source_and_rebuild_recovers_manifest_after_write_failure(
    tmp_path, monkeypatch
):
    session, store = _store(tmp_path)
    path = "raw/response.bin"
    payload = b"durable"
    store.append_bytes(path, payload)
    digest = hashlib.sha256(payload).hexdigest()
    original_write_manifest = reverse_evidence.reverse_project._write_manifest

    def fail_manifest_write(manifest_path, manifest):
        if manifest_path == session.manifest_path:
            raise reverse_evidence.reverse_project.ProjectError("injected failure")
        return original_write_manifest(manifest_path, manifest)

    monkeypatch.setattr(reverse_evidence.reverse_project, "_write_manifest", fail_manifest_write)
    with pytest.raises(EvidenceError):
        store.register_artifact(path, digest, len(payload), "response")

    monkeypatch.setattr(
        reverse_evidence.reverse_project, "_write_manifest", original_write_manifest
    )
    index_path = EvidenceStore(session).rebuild_index()

    manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        {"path": path, "sha256": digest, "size": len(payload), "kind": "response"}
    ]
    assert json.loads(index_path.read_text(encoding="utf-8"))["artifacts"][0]["path"] == path


@pytest.mark.parametrize(
    ("size", "sha256"),
    [(999, hashlib.sha256(b"actual").hexdigest()), (6, "0" * 64)],
)
def test_register_artifact_rejects_metadata_mismatch(tmp_path, size, sha256):
    session, store = _store(tmp_path)
    path = "raw/actual.bin"
    store.append_bytes(path, b"actual")

    with pytest.raises(EvidenceError):
        store.register_artifact(path, sha256, size, "response")

    assert not (session.path / "raw/artifacts.jsonl").exists()


def test_register_artifact_requires_existing_regular_file(tmp_path):
    session, store = _store(tmp_path)

    with pytest.raises(EvidenceError):
        store.register_artifact("raw/missing.bin", "0" * 64, 0, "response")


def test_duplicate_metadata_uses_full_tie_breaker(tmp_path):
    project = ReverseProject.open(tmp_path / "p")
    first_session = project.create_session()
    second_session = project.create_session()
    first_store = EvidenceStore(first_session)
    second_store = EvidenceStore(second_session)
    payload = b"same"
    digest = hashlib.sha256(payload).hexdigest()
    path = "raw/same.bin"
    for store in (first_store, second_store):
        store.append_bytes(path, payload)
    first_store.register_artifact(path, digest, len(payload), "z-kind")
    first_store.register_artifact(path, digest, len(payload), "a-kind")
    second_store.register_artifact(path, digest, len(payload), "a-kind")
    second_store.register_artifact(path, digest, len(payload), "z-kind")

    first_index = json.loads(first_store.rebuild_index().read_text(encoding="utf-8"))
    second_index = json.loads(second_store.rebuild_index().read_text(encoding="utf-8"))
    first_index.pop("session_id")
    second_index.pop("session_id")

    assert first_index == second_index
    assert [item["kind"] for item in first_index["artifacts"]] == ["a-kind", "z-kind"]


def test_register_artifact_rejects_completed_session_without_writing_registry(tmp_path):
    session, store = _store(tmp_path)
    session.close()

    with pytest.raises(EvidenceError):
        store.register_artifact("raw/late.bin", "0" * 64, 0, "response")

    assert not (session.path / "raw/artifacts.jsonl").exists()


def test_finalize_builds_index_before_marking_session_complete(tmp_path):
    session, store = _store(tmp_path)
    store.append_bytes("raw/response.bin", b"complete")

    index_path = store.finalize()

    assert index_path.exists()
    assert json.loads(session.manifest_path.read_text(encoding="utf-8"))["status"] == "complete"


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
