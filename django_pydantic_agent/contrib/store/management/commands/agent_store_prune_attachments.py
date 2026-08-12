from __future__ import annotations

from argparse import ArgumentParser
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from django_pydantic_agent.contrib.store.models import StoredAttachment
from django_pydantic_agent.contrib.store.types.attachment_deletion import AttachmentDeletion
from django_pydantic_agent.contrib.store.utils import (
    delete_attachments,
    preview_attachment_deletion,
    unreferenced_attachments,
)

# Threshold units, as seconds. Hours are the unit a bare number is read in.
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_DEFAULT_UNIT = _UNITS["h"]
_DEFAULT_AGE = "24h"


class Command(BaseCommand):
    """Delete uploads no conversation refers to and that are old enough to be safe.

    Deleting a conversation already takes its attachments with it. What that
    cascade can never see is the upload that was never sent: the user picked a
    file, the composer uploaded it, and then the message was never posted. It
    belongs to no conversation, so nothing will ever come looking for it.

    Which is also why age is not optional. An upload that landed thirty seconds
    ago and is still sitting in the composer is indistinguishable, by references
    alone, from one abandoned last month — a sweep of everything unreferenced
    would delete files out from under a user mid-compose. ``--older-than``
    defaults to 24 hours, and is a floor on how long an upload has to survive
    before it can be considered abandoned; set it to comfortably more than the
    longest a message may sit unsent in your product.

    Nothing schedules this. The library deletes on a cascade the operator can
    reason about and on a command the operator runs; it never runs a timer
    nobody asked for.
    """

    help = "Delete unreferenced attachments older than a threshold (default 24h)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--older-than",
            default=_DEFAULT_AGE,
            help=(
                "Minimum age before an unreferenced attachment may be deleted, as "
                "a number with a unit: 30m, 24h, 7d. A bare number means hours. "
                f"Default {_DEFAULT_AGE}."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted, and delete nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        age = _parse_age(options["older_than"])
        cutoff = timezone.now() - age
        candidates = list(
            unreferenced_attachments(StoredAttachment.objects.all())
            .filter(created_at__lt=cutoff)
            .order_by("pk")
        )
        if options["dry_run"]:
            self._report("Would delete", preview_attachment_deletion(candidates))
            return
        self._report("Deleted", delete_attachments(candidates))

    def _report(self, verb: str, deletion: AttachmentDeletion) -> None:
        self.stdout.write(
            f"{verb} {deletion.rows} attachment row(s) and {deletion.blobs} stored "
            f"file(s), freeing {deletion.bytes_freed} byte(s)."
        )


def _parse_age(value: str) -> timedelta:
    """A ``30m`` / ``24h`` / ``7d`` threshold, or hours when the unit is left off.

    Rejected rather than clamped when it makes no sense: a negative threshold
    would put the cutoff in the future and sweep away uploads that have not
    happened yet, which is the one outcome this command exists to prevent.
    """
    text = value.strip().lower()
    unit = _UNITS.get(text[-1:])
    amount = text if unit is None else text[:-1]
    try:
        number = float(amount)
    except ValueError:
        raise CommandError(
            f"Could not read --older-than {value!r}. Give a number with an "
            "optional s/m/h/d unit, for example 24h."
        ) from None
    if number < 0:
        raise CommandError("--older-than must not be negative.")
    return timedelta(seconds=number * (_DEFAULT_UNIT if unit is None else unit))


__all__ = ["Command"]
