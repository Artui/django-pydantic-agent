from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage

from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation
from django_pydantic_agent.contrib.store.utils import (
    delete_attachments,
    hash_file,
    preview_attachment_deletion,
    unreferenced_attachments,
)

pytestmark = pytest.mark.django_db


class _CountingFile(File):
    """A file that records how many reads its chunking did, and how large."""

    def __init__(self, content: bytes) -> None:
        super().__init__(BytesIO(content), name="counted.bin")
        self.reads: list[int] = []

    def read(self, num_bytes: int | None = None) -> bytes:
        data = super().read(num_bytes)
        self.reads.append(len(data))
        return data


def _attachment(
    attachment_id: str,
    *,
    owner_id: str = "7",
    content: bytes = b"hello",
    size: int | None = None,
    stored_name: str | None = None,
) -> StoredAttachment:
    """A saved attachment row, writing bytes only when it needs its own blob."""
    row = StoredAttachment(
        attachment_id=attachment_id,
        owner_id=owner_id,
        name=f"{attachment_id}.txt",
        size=len(content) if size is None else size,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    if stored_name is None:
        row.file.save(attachment_id, ContentFile(content), save=False)
    else:
        row.file = stored_name
    row.save()
    return row


def test_hash_file_matches_hashlib() -> None:
    assert hash_file(ContentFile(b"hello")) == hashlib.sha256(b"hello").hexdigest()


def test_hash_file_of_an_empty_file() -> None:
    assert hash_file(ContentFile(b"")) == hashlib.sha256(b"").hexdigest()


def test_hash_file_reads_a_large_file_in_chunks() -> None:
    content = bytes(range(256)) * 1024  # 256 KiB
    handle = _CountingFile(content)

    assert hash_file(handle) == hashlib.sha256(content).hexdigest()
    # Four full chunks and the empty read that ends the loop — never the file
    # as one 256 KiB string.
    assert len(handle.reads) > 1
    assert max(handle.reads) == 64 * 1024


def test_unreferenced_attachments_excludes_linked_rows() -> None:
    linked = _attachment("a1")
    loose = _attachment("a2", content=b"other")
    conversation = StoredConversation.objects.create(thread_id="t1", owner_id="7")
    conversation.attachments.add(linked)

    found = unreferenced_attachments(StoredAttachment.objects.all())
    assert [row.attachment_id for row in found] == [loose.attachment_id]


def test_delete_attachments_removes_row_and_blob() -> None:
    row = _attachment("a1")
    stored_name = row.file.name

    result = delete_attachments([row])

    assert (result.rows, result.blobs, result.bytes_freed) == (1, 1, 5)
    assert not StoredAttachment.objects.exists()
    assert not default_storage.exists(stored_name)


def test_delete_attachments_keeps_a_blob_another_row_shares() -> None:
    first = _attachment("a1")
    _attachment("a2", stored_name=first.file.name)

    result = delete_attachments([first])

    assert (result.rows, result.blobs, result.bytes_freed) == (1, 0, 0)
    assert default_storage.exists(first.file.name)


def test_delete_attachments_ignores_a_row_with_no_stored_file() -> None:
    row = StoredAttachment.objects.create(attachment_id="a1", owner_id="7", size=5)

    result = delete_attachments([row])

    assert (result.rows, result.blobs, result.bytes_freed) == (1, 0, 0)


def test_preview_reports_what_deleting_would_free() -> None:
    rows = [_attachment("a1"), _attachment("a2", content=b"other bytes")]
    assert preview_attachment_deletion(rows) == delete_attachments(rows)


def test_preview_counts_a_shared_blob_once() -> None:
    first = _attachment("a1")
    second = _attachment("a2", stored_name=first.file.name)

    result = preview_attachment_deletion([first, second])

    assert (result.rows, result.blobs, result.bytes_freed) == (2, 1, 5)


def test_preview_keeps_a_blob_a_row_outside_the_set_holds() -> None:
    first = _attachment("a1")
    _attachment("a2", stored_name=first.file.name)

    result = preview_attachment_deletion([first])

    assert (result.rows, result.blobs, result.bytes_freed) == (1, 0, 0)


def test_preview_ignores_a_row_with_no_stored_file() -> None:
    row = StoredAttachment.objects.create(attachment_id="a1", owner_id="7", size=5)

    result = preview_attachment_deletion([row])

    assert (result.rows, result.blobs, result.bytes_freed) == (1, 0, 0)


def test_preview_of_nothing() -> None:
    assert preview_attachment_deletion([]).rows == 0
