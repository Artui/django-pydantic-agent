from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation
from django_pydantic_agent.persistence.utils import message_field


def reconcile_conversation_attachments(
    conversation: StoredConversation, messages: Iterable[Any]
) -> None:
    """Point ``conversation`` at exactly the attachments its messages quote.

    The auto-resolver behind attachment lifecycle. Nothing on the wire changes
    and no client changes: the ids are already in the payload, because the web
    component augments the user message it posts with an ``attachments`` array of
    the refs the composer uploaded (``id`` / ``name`` / ``mime`` / ``size``). The
    protocol does not declare that field, but it survives into the stored message
    list, and reading it here is what turns "an id in some JSON" into a relation
    the database can enforce a lifecycle on.

    Reconciled rather than appended: a message removed from the thread on a
    re-save drops its reference too, so the relation always describes the
    conversation as it stands now.

    **Owner-scoped**, and that is the load-bearing part. An id is resolved only
    against the attachments of the conversation's own owner, so a client that
    posts a guessed or copied id links nothing — the same rule that makes
    ``AttachmentStore.open`` return ``None`` across owners.

    The parse is deliberately total: a message that is not a mapping, an
    ``attachments`` field of the wrong type, an entry with no usable ``id`` — all
    are skipped in silence. This runs inside a conversation save, so a shape
    change upstream must degrade to "no reference", never to an exception that
    loses the user's message.

    An id that resolves to nothing simply produces no link. That is not an error
    either: an attachment may have been deleted through the store while the
    thread that mentions it lives on.
    """
    quoted = _quoted_ids(messages)
    conversation.attachments.set(
        StoredAttachment.objects.filter(owner_id=conversation.owner_id, attachment_id__in=quoted)
    )


def _quoted_ids(messages: Iterable[Any]) -> set[str]:
    """Every attachment id the messages mention, in one pass, deduplicated.

    Read from *any* message rather than only user turns. The manifest a
    transport builds for the model is right to read user messages alone (a
    forged assistant turn must not inject files into the prompt); this is the
    opposite trade. Over-reading here can only link an attachment its own owner
    already holds to that owner's own conversation, which changes nothing,
    whereas under-reading marks a file that is plainly in use as an orphan and
    lets the garbage collector delete it.
    """
    quoted: set[str] = set()
    for message in messages:
        entries = message_field(message, "attachments")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            identifier = message_field(entry, "id")
            if isinstance(identifier, str) and identifier.strip():
                quoted.add(identifier)
    return quoted


__all__ = ["reconcile_conversation_attachments"]
