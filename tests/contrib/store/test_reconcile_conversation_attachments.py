from __future__ import annotations

from typing import Any

import pytest
from django.core.files.base import ContentFile

from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation
from django_pydantic_agent.contrib.store.reconcile_conversation_attachments import (
    reconcile_conversation_attachments,
)

pytestmark = pytest.mark.django_db


def _attachment(attachment_id: str, owner_id: str = "7") -> StoredAttachment:
    row = StoredAttachment(attachment_id=attachment_id, owner_id=owner_id, size=5)
    row.file.save(attachment_id, ContentFile(b"hello"), save=False)
    row.save()
    return row


def _conversation(owner_id: str = "7") -> StoredConversation:
    return StoredConversation.objects.create(thread_id="t1", owner_id=owner_id)


def _user_message(*attachments: Any) -> dict[str, Any]:
    return {
        "id": "u1",
        "role": "user",
        "content": "look at this",
        "attachments": list(attachments),
    }


def _linked(conversation: StoredConversation) -> set[str]:
    return set(conversation.attachments.values_list("attachment_id", flat=True))


def test_links_the_ids_a_message_quotes() -> None:
    _attachment("a1")
    _attachment("a2")
    conversation = _conversation()

    reconcile_conversation_attachments(
        conversation,
        [_user_message({"id": "a1", "name": "notes.txt", "mime": "text/plain", "size": 5})],
    )

    assert _linked(conversation) == {"a1"}


def test_reads_the_same_id_from_several_messages_once() -> None:
    _attachment("a1")
    conversation = _conversation()

    reconcile_conversation_attachments(
        conversation, [_user_message({"id": "a1"}), _user_message({"id": "a1"})]
    )

    assert _linked(conversation) == {"a1"}


def test_reads_a_non_user_message_too() -> None:
    # Deliberately permissive: under-reading would mark a file that is plainly
    # in use as an orphan for the collector.
    _attachment("a1")
    conversation = _conversation()

    reconcile_conversation_attachments(
        conversation, [{"id": "m1", "role": "assistant", "attachments": [{"id": "a1"}]}]
    )

    assert _linked(conversation) == {"a1"}


def test_a_resave_adds_and_drops_references() -> None:
    _attachment("a1")
    _attachment("a2")
    conversation = _conversation()

    reconcile_conversation_attachments(conversation, [_user_message({"id": "a1"})])
    assert _linked(conversation) == {"a1"}

    reconcile_conversation_attachments(conversation, [_user_message({"id": "a2"})])
    assert _linked(conversation) == {"a2"}

    reconcile_conversation_attachments(conversation, [_user_message()])
    assert _linked(conversation) == set()


def test_never_links_another_owners_attachment() -> None:
    _attachment("a1", owner_id="99")
    conversation = _conversation(owner_id="7")

    reconcile_conversation_attachments(conversation, [_user_message({"id": "a1"})])

    assert _linked(conversation) == set()


def test_an_id_resolving_to_nothing_links_nothing() -> None:
    conversation = _conversation()

    reconcile_conversation_attachments(conversation, [_user_message({"id": "gone"})])

    assert _linked(conversation) == set()


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(["not a message"], id="message-is-not-a-record"),
        pytest.param([{"id": "u1", "role": "user"}], id="no-attachments-field"),
        pytest.param([{"id": "u1", "attachments": "a1"}], id="attachments-not-a-list"),
        pytest.param([_user_message("a1")], id="entry-is-not-a-record"),
        pytest.param([_user_message({"name": "notes.txt"})], id="entry-has-no-id"),
        pytest.param([_user_message({"id": 7})], id="id-is-not-a-string"),
        pytest.param([_user_message({"id": "   "})], id="id-is-blank"),
    ],
)
def test_malformed_entries_degrade_to_no_reference(messages: list[Any]) -> None:
    _attachment("a1")
    conversation = _conversation()

    reconcile_conversation_attachments(conversation, messages)

    assert _linked(conversation) == set()


def test_reads_message_objects_as_well_as_mappings() -> None:
    class _Message:
        attachments = [{"id": "a1"}]

    _attachment("a1")
    conversation = _conversation()

    reconcile_conversation_attachments(conversation, [_Message()])

    assert _linked(conversation) == {"a1"}
