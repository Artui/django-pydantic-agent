from __future__ import annotations

import json
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from django_pydantic_agent.contrib.store.models import StoredConversation
from django_pydantic_agent.contrib.store.strip_inline_binary_parts import strip_inline_binary_parts


class Command(BaseCommand):
    """Remove inlined file bytes from conversations already stored.

    A transport that hands the model an attachment inline serialises the file
    into the message list as base64, and the message list is persisted. A 2.6 MB
    PDF becomes roughly 3.5 MB of text in one row, loaded whole every time the
    thread is opened and sent to the browser with it. Transports have stopped
    writing them, but a row already written keeps its payload until something
    rewrites it. This is that something, and it is the only thing that is: a
    transport deliberately does not strip what a client posts back, because a
    client may legitimately post inline content of its own, and editing it in
    passing would silently discard it. Stored data is a data migration's job.

    What survives is as important as what goes. Every message keeps its ``id``
    and every field the transport put on it, including the non-standard
    ``attachments`` array the composer rides on a user message — that array is
    what renders the file chips and what the attachment relation is reconciled
    from, so losing it would cost the very files this is tidying up after. The
    rewrite is structural JSON surgery for that reason: nothing is validated or
    re-dumped through a message type, because that is exactly the step that
    drops fields it does not know.

    The bytes are not lost. The attachment itself is untouched in the attachment
    store, and the model reaches it the same way it always does, by id.
    """

    help = "Strip inlined base64 file content from stored conversation messages."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be reclaimed, and change nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]
        rewritten = 0
        reclaimed = 0
        for conversation in StoredConversation.objects.order_by("pk").iterator():
            messages, changed = strip_inline_binary_parts(conversation.messages)
            if not changed:
                continue
            rewritten += 1
            reclaimed += _encoded_size(conversation.messages) - _encoded_size(messages)
            if not dry_run:
                StoredConversation.objects.filter(pk=conversation.pk).update(messages=messages)
        verb = "Would rewrite" if dry_run else "Rewrote"
        self.stdout.write(f"{verb} {rewritten} conversation(s), reclaiming {reclaimed} byte(s).")


def _encoded_size(messages: Any) -> int:
    """The size of ``messages`` as compact JSON.

    Measured on the encoded form because that is what the column stores and what
    crosses the wire to the browser; the in-memory object graph is neither.
    """
    return len(json.dumps(messages, separators=(",", ":"), default=str))


__all__ = ["Command"]
