"""Tests for raw reverse-analysis evidence and deterministic indexes."""

import hashlib
import json
import os
import threading

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

    record = store.register_artifact(path, digest, len(payload), "script")

    manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [record]
    assert record["path"].startswith("raw/.snapshots/")
    assert record["source_path"] == path
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
    assert [item["source_path"] for item in index["artifacts"]] == [
        "raw/a.bin",
        "raw/a.jsonl",
        "raw/z.bin",
    ]
    assert all(
        item["path"] == item["source_path"] or item["path"].startswith("raw/.snapshots/")
        for item in index["artifacts"]
    )
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
    registered = json.loads(
        (session.path / "raw/artifacts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert manifest["artifacts"] == [registered]
    assert registered["source_path"] == path
    assert json.loads(index_path.read_text(encoding="utf-8"))["artifacts"][0]["path"] == registered["path"]


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


def test_registered_artifact_keeps_an_observed_snapshot_after_source_replacement(tmp_path):
    session, store = _store(tmp_path)
    path = session.path / "raw" / "response.bin"
    original = b"observed-before-replacement"
    store.append_bytes("raw/response.bin", original)
    digest = hashlib.sha256(original).hexdigest()
    record = store.register_artifact("raw/response.bin", digest, len(original), "response")

    replacement = path.with_name("replacement.bin")
    replacement.write_bytes(b"different-after-registration")
    os.replace(replacement, path)

    index = json.loads(store.rebuild_index().read_text(encoding="utf-8"))
    artifact = next(item for item in index["artifacts"] if item["source_path"] == "raw/response.bin")
    assert artifact["path"] == record["path"]
    assert artifact["content_sha256"] == digest
    assert artifact["path"].startswith("raw/.snapshots/")


def test_finalize_builds_index_before_marking_session_complete(tmp_path):
    session, store = _store(tmp_path)
    store.append_bytes("raw/response.bin", b"complete")

    index_path = store.finalize()

    assert index_path.exists()
    assert json.loads(session.manifest_path.read_text(encoding="utf-8"))["status"] == "complete"


def test_direct_session_close_builds_durable_index(tmp_path):
    session, store = _store(tmp_path)
    store.append_bytes("raw/response.bin", b"direct-close")

    session.close()

    index_path = session.project.indexes_dir / f"{session.session_id}.json"
    assert index_path.exists()
    assert json.loads(index_path.read_text(encoding="utf-8"))["files"] == {
        "raw/response.bin": {
            "bytes": len(b"direct-close"),
            "event_count": 0,
            "sha256": hashlib.sha256(b"direct-close").hexdigest(),
        }
    }


def test_finalize_holds_session_lock_for_append_snapshot(tmp_path, monkeypatch):
    session, store = _store(tmp_path)
    store.append_bytes("raw/before.bin", b"before")
    file_info_started = threading.Event()
    release_file_info = threading.Event()
    append_finished = threading.Event()
    append_errors = []
    original_file_snapshot = store._file_snapshot

    def delayed_file_snapshot(relative, parts):
        file_info_started.set()
        assert release_file_info.wait(2)
        return original_file_snapshot(relative, parts)

    monkeypatch.setattr(store, "_file_snapshot", delayed_file_snapshot)
    finalize_errors = []

    def finalize():
        try:
            store.finalize()
        except Exception as exc:  # pragma: no cover - assertion below reports it
            finalize_errors.append(exc)

    finalize_thread = threading.Thread(target=finalize)
    finalize_thread.start()
    assert file_info_started.wait(2)

    def append_late():
        try:
            store.append_bytes("raw/after.bin", b"after")
        except EvidenceError as exc:
            append_errors.append(exc)
        finally:
            append_finished.set()

    append_thread = threading.Thread(target=append_late)
    append_thread.start()
    assert not append_finished.wait(0.2)

    release_file_info.set()
    finalize_thread.join(2)
    append_thread.join(2)

    assert not finalize_errors
    assert not finalize_thread.is_alive()
    assert not append_thread.is_alive()
    index = json.loads(
        (session.project.indexes_dir / f"{session.session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert sorted(index["files"]) == ["raw/before.bin"]
    assert len(append_errors) == 1
    assert not (session.path / "raw/after.bin").exists()


def test_index_symlink_replacement_fails_without_outside_write(tmp_path):
    session, store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    indexes = session.project.indexes_dir
    indexes.rmdir()
    indexes.symlink_to(outside, target_is_directory=True)

    with pytest.raises(EvidenceError):
        store.rebuild_index()

    assert not (outside / f"{session.session_id}.json").exists()


def test_direct_session_close_delegates_to_evidence_finalization(tmp_path):
    session, store = _store(tmp_path)
    store.append_bytes("raw/response.bin", b"complete")

    session.close()

    index_path = session.project.indexes_dir / f"{session.session_id}.json"
    assert index_path.exists()
    assert json.loads(session.manifest_path.read_text(encoding="utf-8"))["status"] == "complete"


def test_finalize_holds_evidence_lock_through_index_and_close(tmp_path, monkeypatch):
    session, store = _store(tmp_path)
    store.append_bytes("raw/before.bin", b"before")
    replace_started = threading.Event()
    release_replace = threading.Event()
    append_finished = threading.Event()
    append_errors = []
    original_rename = reverse_evidence.os.rename

    def delayed_rename(source, destination, *args, **kwargs):
        if destination == f"{session.session_id}.json" and kwargs.get("dst_dir_fd") is not None:
            replace_started.set()
            assert release_replace.wait(2)
        return original_rename(source, destination, *args, **kwargs)

    def append_after_snapshot():
        try:
            store.append_bytes("raw/after.bin", b"after")
        except EvidenceError as exc:
            append_errors.append(exc)
        finally:
            append_finished.set()

    monkeypatch.setattr(reverse_evidence.os, "rename", delayed_rename)
    finalization = threading.Thread(target=store.finalize)
    finalization.start()
    assert replace_started.wait(2)

    writer = threading.Thread(target=append_after_snapshot)
    writer.start()
    assert not append_finished.wait(0.2)
    release_replace.set()
    finalization.join(2)
    writer.join(2)

    assert not finalization.is_alive()
    assert not writer.is_alive()
    assert len(append_errors) == 1
    assert json.loads(session.manifest_path.read_text(encoding="utf-8"))["status"] == "complete"
    index = json.loads(
        (session.project.indexes_dir / f"{session.session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["path"] for item in index["artifacts"]] == ["raw/before.bin"]
    assert not (session.path / "raw/after.bin").exists()


def test_index_directory_symlink_replacement_is_rejected_without_escape(tmp_path):
    session, store = _store(tmp_path)
    store.append_bytes("raw/response.bin", b"raw")
    outside = tmp_path / "outside-indexes"
    outside.mkdir()
    indexes = session.project.indexes_dir
    indexes.rename(tmp_path / "original-indexes")
    indexes.symlink_to(outside, target_is_directory=True)

    with pytest.raises(EvidenceError):
        store.rebuild_index()

    assert not (outside / f"{session.session_id}.json").exists()


def test_register_artifact_rejects_path_replacement_after_fd_digest(tmp_path, monkeypatch):
    session, store = _store(tmp_path)
    path = session.path / "raw/response.bin"
    store.append_bytes("raw/response.bin", b"before")
    original_digest = store._digest_fd
    replaced = False

    def digest_then_replace(descriptor):
        nonlocal replaced
        result = original_digest(descriptor)
        if not replaced:
            replaced = True
            replacement = path.with_name("replacement.bin")
            replacement.write_bytes(b"after")
            replacement.replace(path)
        return result

    monkeypatch.setattr(store, "_digest_fd", digest_then_replace)

    with pytest.raises(EvidenceError):
        store.register_artifact(
            "raw/response.bin",
            hashlib.sha256(b"before").hexdigest(),
            len(b"before"),
            "response",
        )

    assert not (session.path / "raw/artifacts.jsonl").exists()


def test_register_artifact_snapshots_bytes_before_source_replacement(tmp_path, monkeypatch):
    session, store = _store(tmp_path)
    source = "raw/response.bin"
    source_path = session.path / source
    observed = b"observed-before-replacement"
    replacement = b"replacement-after-validation"
    store.append_bytes(source, observed)
    original_append = store._append_unlocked
    replaced = False

    def replace_source_before_registry(relative_path, data):
        nonlocal replaced
        if relative_path == "raw/artifacts.jsonl" and not replaced:
            replacement_path = source_path.with_name("replacement.bin")
            replacement_path.write_bytes(replacement)
            replacement_path.replace(source_path)
            replaced = True
        return original_append(relative_path, data)

    monkeypatch.setattr(store, "_append_unlocked", replace_source_before_registry)
    digest = hashlib.sha256(observed).hexdigest()

    record = store.register_artifact(source, digest, len(observed), "response")

    assert record["source_path"] == source
    expected_snapshot = (
        "raw/.snapshots/"
        + hashlib.sha256((source + digest).encode("utf-8")).hexdigest()
        + ".bin"
    )
    assert record["path"] == expected_snapshot
    snapshot_path = session.path / record["path"]
    assert snapshot_path.read_bytes() == observed
    registry = [
        json.loads(line)
        for line in (session.path / "raw/artifacts.jsonl").read_text().splitlines()
    ]
    assert registry == [record]

    index = json.loads(store.rebuild_index().read_text(encoding="utf-8"))
    artifact = next(item for item in index["artifacts"] if item["kind"] == "response")
    assert artifact["path"] == record["path"]
    assert artifact["source_path"] == source
    assert artifact["sha256"] == digest
    assert artifact["size"] == len(observed)
    assert artifact["content_sha256"] == digest
    assert index["files"][source]["sha256"] == hashlib.sha256(replacement).hexdigest()
    assert all("/.snapshots/" not in path for path in index["files"])


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
