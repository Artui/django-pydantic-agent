from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation
from django_pydantic_agent.persistence.utils import message_field


def reconcile_conversation_attachments(
    conversation: StoredConversation, messages: Iterable[Any]
) -> None:
    """Point ``conversation`` at exactly the attachments its messages quote.

    The resolver behind attachment lifecycle, needing no wire or client change:
    the web component already augments each user message with an ``attachments``
    array of the refs the composer uploaded, and that undeclared field survives
    into the stored message list. Reading it here turns an id in some JSON into a
    relation the database can enforce a lifecycle on.

    Reconciled rather than appended, so a message dropped on a re-save drops its
    reference too and the relation always describes the conversation as it
    stands.

    **Owner-scoped**, which is the load-bearing part: an id resolves only against
    the attachments of the conversation's own owner, so a guessed or copied id
    links nothing — the rule that makes ``AttachmentStore.open`` return ``None``
    across owners.

    The parse is total. A message that is not a mapping, an ``attachments`` field
    of the wrong type, an entry with no usable ``id``, an id resolving to an
    attachment since deleted: all degrade to "no reference" in silence. This runs
    inside a conversation save, where an exception would lose the user's message.
    """
    quoted = _quoted_ids(messages)
    conversation.attachments.set(
        StoredAttachment.objects.filter(owner_id=conversation.owner_id, attachment_id__in=quoted)
    )


def _quoted_ids(messages: Iterable[Any]) -> set[str]:
    """Every attachment id the messages mention, deduplicated.

    Read from *any* message, not only user turns — the opposite trade from the
    manifest a transport builds for the model, which is right to read user turns
    alone so a forged assistant turn cannot inject files into the prompt.
    Over-reading here only links an owner's own attachment to their own
    conversation; under-reading marks a file plainly in use as an orphan and lets
    the collector delete it.
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
