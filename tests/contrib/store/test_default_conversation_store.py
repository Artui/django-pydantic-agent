from __future__ import annotations

from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from django_pydantic_agent.contrib.store.default_conversation_store import DefaultConversationStore
from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation
from django_pydantic_agent.persistence.types.conversation import Conversation

pytestmark = pytest.mark.django_db


def _conv(
    thread_id: str = "t1", text: str = "book a flight", owner_id: str | None = "7"
) -> Conversation:
    return Conversation(
        thread_id=thread_id,
        messages=[{"id": "u1", "role": "user", "content": text}],
        owner_id=owner_id,
    )


def _conv_with(
    *attachment_ids: str,
    thread_id: str = "t1",
    owner_id: str | None = "7",
) -> Conversation:
    message: dict[str, Any] = {
        "id": "u1",
        "role": "user",
        "content": "look at this",
        "attachments": [{"id": attachment_id} for attachment_id in attachment_ids],
    }
    return Conversation(thread_id=thread_id, messages=[message], owner_id=owner_id)


def _attachment(attachment_id: str, owner_id: str = "7") -> StoredAttachment:
    row = StoredAttachment(attachment_id=attachment_id, owner_id=owner_id, size=5)
    row.file.save(attachment_id, ContentFile(b"hello"), save=False)
    row.save()
    return row


def test_store_then_fetch_round_trips() -> None:
    store = DefaultConversationStore()
    store._store(_conv(), "7")
    fetched = store._fetch("t1", "7")
    assert fetched is not None
    assert fetched.thread_id == "t1"
    assert [m["id"] for m in fetched.messages] == ["u1"]
    assert fetched.owner_id == "7"


def test_fetch_missing_returns_none() -> None:
    assert DefaultConversationStore()._fetch("absent", "7") is None


def test_store_denormalizes_title_and_preview() -> None:
    store = DefaultConversationStore()
    store._store(_conv(text="book a flight"), "7")
    (meta,) = store._list("7", None)
    assert meta.thread_id == "t1"
    assert meta.title == "book a flight"
    assert meta.preview == "book a flight"
    assert meta.updated_at is not None
    assert meta.owner_id == "7"


def test_list_is_owner_scoped() -> None:
    store = DefaultConversationStore()
    store._store(_conv("t1"), "7")
    store._store(_conv("t2"), "7")
    store._store(_conv("t3"), "99")  # a different owner
    assert {meta.thread_id for meta in store._list("7", None)} == {"t1", "t2"}


def test_resave_preserves_title_and_refreshes_preview() -> None:
    store = DefaultConversationStore()
    store._store(_conv(text="book a flight"), "7")
    store._store(
        Conversation(
            thread_id="t1",
            messages=[
                {"id": "u1", "role": "user", "content": "book a flight"},
                {"id": "u2", "role": "user", "content": "actually, cancel it"},
            ],
            owner_id="7",
        ),
        "7",
    )
    (meta,) = store._list("7", None)
    assert meta.title == "book a flight"  # title frozen at first message
    assert meta.preview == "actually, cancel it"  # preview follows the latest


def test_rename_updates_title() -> None:
    store = DefaultConversationStore()
    store._store(_conv(), "7")
    store._rename("t1", "Trip planning", "7")
    (meta,) = store._list("7", None)
    assert meta.title == "Trip planning"


def test_remove_deletes() -> None:
    store = DefaultConversationStore()
    store._store(_conv(), "7")
    store._remove("t1", "7")
    assert store._fetch("t1", "7") is None


def test_anonymous_owner_normalized_to_empty_string() -> None:
    store = DefaultConversationStore()
    store._store(_conv(owner_id=None), None)
    fetched = store._fetch("t1", None)
    assert fetched is not None
    assert fetched.owner_id is None  # stored "" maps back to None
    assert StoredConversation.objects.get(thread_id="t1").owner_id == ""
    (meta,) = store._list(None, None)
    assert meta.owner_id is None


def test_list_applies_limit() -> None:
    store = DefaultConversationStore()
    for i in range(5):
        store._store(_conv(f"t{i}"), "7")
    assert len(store._list("7", 2)) == 2
    assert len(store._list("7", None)) == 5


def test_exists_is_owner_scoped_and_reads_no_body() -> None:
    store = DefaultConversationStore()
    store._store(_conv("t1"), "7")
    assert store._exists("t1", "7") is True
    assert store._exists("t1", "99") is False  # a different owner
    assert store._exists("absent", "7") is False


def test_store_references_the_attachments_its_messages_quote() -> None:
    store = DefaultConversationStore()
    _attachment("a1")
    store._store(_conv_with("a1"), "7")
    row = StoredConversation.objects.get(thread_id="t1")
    assert set(row.attachments.values_list("attachment_id", flat=True)) == {"a1"}


def test_resave_reconciles_references_from_the_new_messages() -> None:
    store = DefaultConversationStore()
    _attachment("a1")
    _attachment("a2")
    store._store(_conv_with("a1"), "7")
    store._store(_conv_with("a2"), "7")
    row = StoredConversation.objects.get(thread_id="t1")
    assert set(row.attachments.values_list("attachment_id", flat=True)) == {"a2"}


def test_remove_deletes_attachments_nothing_else_references() -> None:
    store = DefaultConversationStore()
    attachment = _attachment("a1")
    stored_name = attachment.file.name
    store._store(_conv_with("a1"), "7")

    store._remove("t1", "7")

    assert not StoredAttachment.objects.filter(attachment_id="a1").exists()
    assert not default_storage.exists(stored_name)


def test_remove_keeps_an_attachment_another_thread_references() -> None:
    store = DefaultConversationStore()
    attachment = _attachment("a1")
    store._store(_conv_with("a1", thread_id="t1"), "7")
    store._store(_conv_with("a1", thread_id="t2"), "7")

    store._remove("t1", "7")

    assert StoredAttachment.objects.filter(attachment_id="a1").exists()
    assert default_storage.exists(attachment.file.name)


def test_remove_leaves_another_owners_attachments_alone() -> None:
    store = DefaultConversationStore()
    _attachment("a1", owner_id="99")
    store._store(_conv_with("a1", owner_id="7"), "7")

    store._remove("t1", "7")

    # The id was never resolvable across owners, so it was never referenced.
    assert StoredAttachment.objects.filter(attachment_id="a1").exists()
