from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation

pytestmark = pytest.mark.django_db

COMMAND = "agent_store_prune_attachments"


def _attachment(
    attachment_id: str = "a1",
    *,
    age: timedelta = timedelta(days=3),
    content: bytes = b"hello",
) -> StoredAttachment:
    row = StoredAttachment(attachment_id=attachment_id, owner_id="7", size=len(content))
    row.file.save(attachment_id, ContentFile(content), save=False)
    row.save()
    # ``created_at`` is ``auto_now_add``, so the age has to be written back.
    StoredAttachment.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)
    row.refresh_from_db()
    return row


def _run(*args: str) -> str:
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


def test_deletes_an_old_unreferenced_upload() -> None:
    row = _attachment()

    output = _run()

    assert not StoredAttachment.objects.exists()
    assert not default_storage.exists(row.file.name)
    assert "Deleted 1 attachment row(s) and 1 stored file(s), freeing 5 byte(s)." in output


def test_keeps_an_upload_younger_than_the_threshold() -> None:
    _attachment(age=timedelta(seconds=30))

    _run()

    # Still in the composer, not yet sent: zero references, and none expected.
    assert StoredAttachment.objects.exists()


def test_keeps_a_referenced_attachment_however_old() -> None:
    row = _attachment(age=timedelta(days=400))
    conversation = StoredConversation.objects.create(thread_id="t1", owner_id="7")
    conversation.attachments.add(row)

    _run()

    assert StoredAttachment.objects.exists()


@pytest.mark.parametrize(
    ("threshold", "deleted"),
    [
        pytest.param("30m", True, id="minutes"),
        pytest.param("3600s", True, id="seconds"),
        pytest.param("7d", False, id="days"),
        pytest.param("96", False, id="bare-number-means-hours"),
    ],
)
def test_the_threshold_takes_a_unit(threshold: str, deleted: bool) -> None:
    _attachment(age=timedelta(days=3))

    _run("--older-than", threshold)

    assert StoredAttachment.objects.exists() is not deleted


def test_a_dry_run_reports_and_deletes_nothing() -> None:
    row = _attachment()

    output = _run("--dry-run")

    assert "Would delete 1 attachment row(s) and 1 stored file(s), freeing 5 byte(s)." in output
    assert StoredAttachment.objects.exists()
    assert default_storage.exists(row.file.name)


def test_an_unreadable_threshold_is_refused() -> None:
    with pytest.raises(CommandError, match="Could not read --older-than"):
        _run("--older-than", "soon")


def test_a_negative_threshold_is_refused() -> None:
    # It would put the cutoff in the future and sweep uploads still in flight.
    with pytest.raises(CommandError, match="must not be negative"):
        _run("--older-than=-1h")
