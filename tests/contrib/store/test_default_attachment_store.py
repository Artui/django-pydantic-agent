from __future__ import annotations

import hashlib
import logging
from io import BytesIO

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile

from django_pydantic_agent.contrib.store.default_attachment_store import DefaultAttachmentStore
from django_pydantic_agent.contrib.store.models import StoredAttachment

pytestmark = pytest.mark.django_db


def _upload(
    name: str | None = "notes.txt",
    content: bytes = b"hello",
    content_type: str | None = "text/plain",
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def _streamed_upload(content: bytes, name: str = "big.bin") -> UploadedFile:
    """An upload whose ``chunks()`` really yields more than one chunk.

    ``SimpleUploadedFile`` is an ``InMemoryUploadedFile``, and Django's override
    there hands back the whole file as a single chunk — fine for a small
    fixture, useless for showing that hashing a large upload streams. The plain
    ``UploadedFile`` inherits ``File.chunks()``, which reads in fixed-size
    pieces, so it is the shape a large upload actually arrives in.
    """
    return UploadedFile(
        file=BytesIO(content),
        name=name,
        content_type="application/octet-stream",
        size=len(content),
    )


def _stored_name(attachment_id: str) -> str:
    return StoredAttachment.objects.get(attachment_id=attachment_id).file.name


def _distinct_blobs() -> int:
    """How many stored files the attachment rows point at between them.

    Counted from the rows rather than by listing storage: the in-memory backend
    is a process-wide singleton that outlives the per-test database rollback, so
    its directory listing carries every other test's uploads too.
    """
    return StoredAttachment.objects.values("file").distinct().count()


def test_save_then_open_round_trips() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    assert ref.name == "notes.txt"
    assert ref.mime == "text/plain"
    assert ref.size == 5

    opened = store._open(ref.id, "7")
    assert opened is not None
    with opened.content as handle:
        assert handle.read() == b"hello"
    assert opened.ref.id == ref.id
    assert opened.ref.name == "notes.txt"


def test_open_missing_returns_none() -> None:
    assert DefaultAttachmentStore()._open("absent", "7") is None


def test_open_is_owner_scoped() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    # A different owner can't resolve the same id.
    assert store._open(ref.id, "99") is None


def test_save_strips_path_from_name() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(name="sub/dir/notes.txt"), "7")
    assert ref.name == "notes.txt"


def test_save_without_name_falls_back() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(name=None), "7")
    assert ref.name == "attachment"


def test_save_without_content_type_stores_empty_mime() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(content_type=None), "7")
    assert ref.mime == ""


def test_save_empty_file_has_zero_size() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(content=b""), "7")
    assert ref.size == 0


def test_anonymous_owner_normalized_to_empty_string() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), None)
    assert store._open(ref.id, None) is not None
    assert StoredAttachment.objects.get(attachment_id=ref.id).owner_id == ""


def test_remove_deletes_row_and_bytes() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    stored_name = _stored_name(ref.id)
    store._remove(ref.id, "7")
    assert store._open(ref.id, "7") is None
    assert not StoredAttachment.objects.filter(attachment_id=ref.id).exists()
    assert not default_storage.exists(stored_name)


def test_remove_missing_is_noop() -> None:
    # No row for this id — must not raise.
    assert DefaultAttachmentStore()._remove("absent", "7") is None


def test_save_caps_long_filename_preserving_extension() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(name="a" * 300 + ".txt"), "7")
    assert len(ref.name) == 255
    assert ref.name.endswith(".txt")
    # The stored row honors the model's max_length too.
    assert StoredAttachment.objects.get(attachment_id=ref.id).name == ref.name


def test_save_hard_truncates_a_long_name_without_extension() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(name="b" * 300), "7")
    assert ref.name == "b" * 255


def test_save_records_the_content_hash() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(content=b"hello"), "7")
    row = StoredAttachment.objects.get(attachment_id=ref.id)
    assert row.sha256 == hashlib.sha256(b"hello").hexdigest()


def test_reuploading_the_same_bytes_reuses_the_blob() -> None:
    store = DefaultAttachmentStore()
    first = store._save(_upload(name="notes.txt", content=b"hello"), "7")
    second = store._save(_upload(name="copy.txt", content=b"hello"), "7")

    # A row each, because the name on the chip belongs to the upload...
    assert first.id != second.id
    assert second.name == "copy.txt"
    # ...but one blob underneath, and no second write to storage.
    assert _stored_name(first.id) == _stored_name(second.id)
    assert _distinct_blobs() == 1

    opened = store._open(second.id, "7")
    assert opened is not None
    with opened.content as handle:
        assert handle.read() == b"hello"


def test_different_bytes_are_not_deduplicated() -> None:
    store = DefaultAttachmentStore()
    first = store._save(_upload(content=b"hello"), "7")
    second = store._save(_upload(content=b"goodbye"), "7")
    assert _stored_name(first.id) != _stored_name(second.id)


def test_deduplication_never_crosses_owners() -> None:
    store = DefaultAttachmentStore()
    mine = store._save(_upload(content=b"hello"), "7")
    theirs = store._save(_upload(content=b"hello"), "99")
    # Identical bytes, two blobs on purpose: a cross-owner hit would disclose
    # that another tenant holds this exact file.
    assert _stored_name(mine.id) != _stored_name(theirs.id)
    assert _distinct_blobs() == 2


def test_a_multi_chunk_upload_is_hashed_and_stored_whole() -> None:
    store = DefaultAttachmentStore()
    content = bytes(range(256)) * 1024  # 256 KiB, four 64 KiB chunks
    ref = store._save(_streamed_upload(content), "7")

    row = StoredAttachment.objects.get(attachment_id=ref.id)
    assert row.sha256 == hashlib.sha256(content).hexdigest()
    # Hashing consumed the stream; the bytes still reached storage in full.
    opened = store._open(ref.id, "7")
    assert opened is not None
    with opened.content as handle:
        assert handle.read() == content


def test_removing_one_of_two_rows_sharing_a_blob_keeps_the_file() -> None:
    store = DefaultAttachmentStore()
    first = store._save(_upload(name="notes.txt", content=b"hello"), "7")
    second = store._save(_upload(name="copy.txt", content=b"hello"), "7")

    store._remove(first.id, "7")

    assert store._open(first.id, "7") is None
    surviving = store._open(second.id, "7")
    assert surviving is not None
    with surviving.content as handle:
        assert handle.read() == b"hello"


def test_removing_the_last_row_sharing_a_blob_deletes_the_file() -> None:
    store = DefaultAttachmentStore()
    first = store._save(_upload(name="notes.txt", content=b"hello"), "7")
    second = store._save(_upload(name="copy.txt", content=b"hello"), "7")
    stored_name = _stored_name(first.id)

    store._remove(first.id, "7")
    assert default_storage.exists(stored_name)
    store._remove(second.id, "7")
    assert not default_storage.exists(stored_name)


# --- a row whose bytes are gone ------------------------------------------------
#
# Rows outlive blobs: a lifecycle rule expires a key, a dump is restored beside an
# empty bucket, a backend is migrated underneath. The row is then a promise the
# storage cannot keep, and the two defects below used to compound -- the read
# raised where the contract says ``None``, and the re-upload that should have
# repaired it adopted the same dead path and wrote nothing.


def test_open_reports_a_missing_blob_as_unavailable() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    default_storage.delete(_stored_name(ref.id))

    # ``None``, not ``FileNotFoundError``: the contract gives a caller one branch
    # for "unavailable" and keeps a missing file indistinguishable from another
    # owner's.
    assert store._open(ref.id, "7") is None


def test_open_logs_the_missing_blob_it_reports(caplog: pytest.LogCaptureFixture) -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    name = _stored_name(ref.id)
    default_storage.delete(name)

    with caplog.at_level(logging.WARNING):
        assert store._open(ref.id, "7") is None

    # Reported to the caller as unavailable, but a storage fault to whoever reads
    # the logs -- the row is still there.
    assert name in caplog.text


def test_a_re_upload_repairs_an_owner_whose_blob_went_missing() -> None:
    store = DefaultAttachmentStore()
    first = store._save(_upload(content=b"hello"), "7")
    dead = _stored_name(first.id)
    default_storage.delete(dead)

    # The user's natural recovery. Identical bytes hash identically, so this is
    # exactly the upload that used to match the dead twin and write nothing.
    second = store._save(_upload(content=b"hello"), "7")

    assert default_storage.exists(_stored_name(second.id))
    opened = store._open(second.id, "7")
    assert opened is not None
    with opened.content as handle:
        assert handle.read() == b"hello"


def test_deduplication_still_skips_the_write_when_the_blob_is_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guard costs one ``exists`` and must not cost the optimisation. Asserted
    # against Storage rather than against the rows: code that wrote a blob and
    # *then* adopted the twin's name would satisfy a row count while orphaning
    # the bytes it had just written.
    store = DefaultAttachmentStore()
    first = store._save(_upload(content=b"hello"), "7")

    writes: list[str] = []
    original = default_storage._save

    def _record(name: str, content: object) -> str:
        writes.append(name)
        return original(name, content)

    monkeypatch.setattr(default_storage, "_save", _record)
    second = store._save(_upload(content=b"hello"), "7")

    assert writes == []
    assert _stored_name(first.id) == _stored_name(second.id)
    assert _distinct_blobs() == 1


def test_dedup_survives_an_owner_whose_oldest_blob_died() -> None:
    # The healed owner holds a dead row and a live one for the same bytes.
    # Consulting only the oldest would find the dead one every time and write a
    # fresh copy on every upload after it -- dedup lost for good, storage growing
    # without bound.
    store = DefaultAttachmentStore()
    first = store._save(_upload(content=b"hello"), "7")
    default_storage.delete(_stored_name(first.id))

    healed = store._save(_upload(content=b"hello"), "7")
    third = store._save(_upload(content=b"hello"), "7")

    assert _stored_name(third.id) == _stored_name(healed.id)
    live = {
        name
        for name in (_stored_name(healed.id), _stored_name(third.id))
        if default_storage.exists(name)
    }
    assert len(live) == 1


def test_open_reports_a_row_that_never_recorded_a_path() -> None:
    # The other way to have no readable bytes. ``FieldFile`` guards an empty path
    # with ``ValueError``, not ``FileNotFoundError``, and the contract draws no
    # line between the two.
    StoredAttachment.objects.create(attachment_id="pathless", owner_id="7", file="")
    store = DefaultAttachmentStore()

    assert store._open("pathless", "7") is None


def test_a_twin_carrying_no_path_is_not_adopted() -> None:
    # A row can exist with no stored path at all, from a write that failed after
    # the INSERT. It offers nothing to adopt, so the upload writes its own bytes
    # rather than pointing at nowhere.
    store = DefaultAttachmentStore()
    digest = hashlib.sha256(b"hello").hexdigest()
    StoredAttachment.objects.create(attachment_id="orphan", owner_id="7", sha256=digest, file="")

    ref = store._save(_upload(content=b"hello"), "7")

    assert _stored_name(ref.id) != ""
    opened = store._open(ref.id, "7")
    assert opened is not None
    with opened.content as handle:
        assert handle.read() == b"hello"
