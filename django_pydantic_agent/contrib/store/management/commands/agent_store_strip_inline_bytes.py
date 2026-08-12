from __future__ import annotations

import json
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from django_pydantic_agent.contrib.store.models import StoredConversation
from django_pydantic_agent.contrib.store.strip_inline_binary_parts import strip_inline_binary_parts


class Command(BaseCommand):
    """Remove inlined file bytes from conversations already stored.

    A row written before transports stopped inlining keeps its base64 payload
    until something rewrites it, and this is the only thing that does: a
    transport does not strip what a client posts back, because a client may
    legitimately post inline content of its own.

    The bytes are not lost — the attachment is untouched in the attachment store,
    and the model reaches it by id as always.
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

    The encoded form is what the column stores and what crosses the wire to the
    browser; the in-memory object graph is neither.
    """
    return len(json.dumps(messages, separators=(",", ":"), default=str))


__all__ = ["Command"]
