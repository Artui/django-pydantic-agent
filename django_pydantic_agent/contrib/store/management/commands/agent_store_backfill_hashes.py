from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from django_pydantic_agent.contrib.store.models import StoredAttachment
from django_pydantic_agent.contrib.store.utils import hash_file


class Command(BaseCommand):
    """Fill in ``sha256`` for attachments stored before the column existed.

    Deduplication compares content hashes, so a row without one can never be
    matched: until it is hashed, re-uploading the file it holds writes a second
    copy of the bytes. This command is how an existing installation catches up.

    It is a command rather than a data migration on purpose. Hashing means
    reading every blob back — over the network, if storage is S3 — and a
    migration that does that runs inside the deploy, holding it open for as long
    as the bucket takes and failing the release if one object is missing. Run
    here, it is interruptible, resumable (it only ever looks at unhashed rows),
    and a missing file is a reported skip rather than a broken deploy.
    """

    help = "Compute the missing sha256 content hashes for stored attachments."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many attachments need hashing, and change nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]
        pending = StoredAttachment.objects.filter(sha256="").order_by("pk")
        if dry_run:
            self.stdout.write(f"{pending.count()} attachment(s) would be hashed.")
            return
        hashed = 0
        skipped = 0
        for attachment in pending.iterator():
            digest = self._digest(attachment)
            if digest is None:
                skipped += 1
                continue
            StoredAttachment.objects.filter(pk=attachment.pk).update(sha256=digest)
            hashed += 1
        self.stdout.write(f"Hashed {hashed} attachment(s), skipped {skipped} unreadable.")

    def _digest(self, attachment: StoredAttachment) -> str | None:
        """The row's content hash, or ``None`` when its bytes cannot be read.

        A row whose blob has gone missing — deleted out of band, or restored from
        a database dump without the bucket behind it — must not stop the run: the
        remaining rows are still worth hashing, and a hash invented for absent
        bytes would be worse than none.
        """
        try:
            with attachment.file.open("rb") as handle:
                return hash_file(handle)
        except (OSError, ValueError) as exc:
            self.stderr.write(f"Skipped attachment {attachment.attachment_id}: {exc}")
            return None


__all__ = ["Command"]
