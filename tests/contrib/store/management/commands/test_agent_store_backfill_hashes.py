from __future__ import annotations

import hashlib
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command

from django_pydantic_agent.contrib.store.models import StoredAttachment

pytestmark = pytest.mark.django_db

COMMAND = "agent_store_backfill_hashes"


def _legacy_attachment(attachment_id: str = "a1", content: bytes = b"hello") -> StoredAttachment:
    """A row as it looked before the hash column existed: bytes, no digest."""
    row = StoredAttachment(attachment_id=attachment_id, owner_id="7", size=len(content))
    row.file.save(attachment_id, ContentFile(content), save=False)
    row.save()
    return row


def _run(*args: str) -> str:
    out = StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def test_hashes_rows_that_have_none() -> None:
    _legacy_attachment(content=b"hello")

    output = _run()

    assert StoredAttachment.objects.get().sha256 == hashlib.sha256(b"hello").hexdigest()
    assert "Hashed 1 attachment(s), skipped 0 unreadable." in output


def test_leaves_an_already_hashed_row_alone() -> None:
    row = _legacy_attachment()
    StoredAttachment.objects.filter(pk=row.pk).update(sha256="a" * 64)

    _run()

    assert StoredAttachment.objects.get().sha256 == "a" * 64


def test_reports_without_writing_on_a_dry_run() -> None:
    _legacy_attachment()

    output = _run("--dry-run")

    assert "1 attachment(s) would be hashed." in output
    assert StoredAttachment.objects.get().sha256 == ""


def test_a_missing_blob_is_skipped_not_fatal() -> None:
    missing = _legacy_attachment("gone")
    default_storage.delete(missing.file.name)
    _legacy_attachment("kept", content=b"other")

    errors = StringIO()
    out = StringIO()
    call_command(COMMAND, stdout=out, stderr=errors)

    assert "Skipped attachment gone" in errors.getvalue()
    assert "Hashed 1 attachment(s), skipped 1 unreadable." in out.getvalue()
    assert StoredAttachment.objects.get(attachment_id="gone").sha256 == ""
    assert StoredAttachment.objects.get(attachment_id="kept").sha256 != ""
