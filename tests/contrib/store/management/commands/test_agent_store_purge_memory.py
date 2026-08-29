from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from django_pydantic_agent.contrib.store.models import StoredMemory, StoredMemoryOperation

pytestmark = pytest.mark.django_db


def _memory(owner_id: str, path: str) -> None:
    StoredMemory.objects.create(owner_id=owner_id, path=path, content="- note", version="v1")


def _run(*args: str) -> str:
    out = StringIO()
    call_command("agent_store_purge_memory", *args, stdout=out)
    return out.getvalue()


def test_it_deletes_every_memory_file_for_the_named_owner() -> None:
    _memory("7", "u-7/main/MEMORY.md")
    _memory("7", "u-7/main/travel.md")
    StoredMemoryOperation.objects.create(owner_id="7", operation_id="op-1", fingerprint="fp")

    output = _run("7")

    assert "Deleted 2 memory file(s) for '7'." in output
    assert not StoredMemory.objects.exists()
    assert not StoredMemoryOperation.objects.exists()


def test_it_leaves_other_owners_alone() -> None:
    _memory("7", "u-7/main/MEMORY.md")
    _memory("8", "u-8/main/MEMORY.md")

    _run("7")

    assert [row.owner_id for row in StoredMemory.objects.all()] == ["8"]


def test_a_dry_run_reports_the_count_and_changes_nothing() -> None:
    _memory("7", "u-7/main/MEMORY.md")

    output = _run("7", "--dry-run")

    assert "1 memory file(s) would be deleted for '7'." in output
    assert StoredMemory.objects.count() == 1


def test_purging_an_owner_with_no_memory_is_not_an_error() -> None:
    output = _run("nobody")

    assert "Deleted 0 memory file(s) for 'nobody'." in output
