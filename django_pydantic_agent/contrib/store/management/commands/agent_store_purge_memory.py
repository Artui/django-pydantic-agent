from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from django_pydantic_agent.contrib.store.default_memory_store import DefaultMemoryStore
from django_pydantic_agent.contrib.store.models import StoredMemory


class Command(BaseCommand):
    """Erase every stored memory file belonging to one owner.

    Memory is durable personal data written *about* a user and replayed into
    every later session, so an erasure request has to be able to reach it. The
    ``MemoryStore`` protocol has no bulk or prefix delete — its five methods are
    ``read``, ``write``, ``delete``, ``list_paths`` and ``get_operation`` — and
    composing them gives an unbounded read-then-delete loop that a concurrent
    ``write_memory`` can lose to. This runs the single statement instead.

    ``owner_id`` is what the store partitions on: the user's pk for an
    authenticated owner, or the ``anon:<session_key>`` bucket for an anonymous
    one. It is **not** the harness namespace, which is derived separately and
    prefixed.
    """

    help = "Delete all stored agent memory for one owner."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("owner_id", help="The resolved owner id to erase memory for.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many memory files would be deleted, and change nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        owner_id: str = options["owner_id"]
        if options["dry_run"]:
            pending = StoredMemory.objects.filter(owner_id=owner_id).count()
            self.stdout.write(f"{pending} memory file(s) would be deleted for {owner_id!r}.")
            return
        deleted = DefaultMemoryStore.purge(owner_id)
        self.stdout.write(f"Deleted {deleted} memory file(s) for {owner_id!r}.")


__all__ = ["Command"]
