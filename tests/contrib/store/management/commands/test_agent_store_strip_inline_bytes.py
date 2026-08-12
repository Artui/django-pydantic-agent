from __future__ import annotations

import base64
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command

from django_pydantic_agent.contrib.store.models import StoredConversation

pytestmark = pytest.mark.django_db

COMMAND = "agent_store_strip_inline_bytes"


def _inlined_pdf(size: int = 4096) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {
            "type": "data",
            "value": base64.b64encode(b"%PDF-1.7" + b"\0" * size).decode(),
            "mime_type": "application/pdf",
        },
        "metadata": None,
    }


def _conversation(*messages: Any, thread_id: str = "t1") -> StoredConversation:
    return StoredConversation.objects.create(
        thread_id=thread_id, owner_id="7", messages=list(messages)
    )


def _run(*args: str) -> str:
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


def test_removes_inlined_bytes_and_reports_what_it_reclaimed() -> None:
    conversation = _conversation({"id": "u1", "role": "user", "content": [_inlined_pdf()]})

    output = _run()

    conversation.refresh_from_db()
    assert conversation.messages == [{"id": "u1", "role": "user", "content": []}]
    assert output.startswith("Rewrote 1 conversation(s), reclaiming ")
    reclaimed = int(output.split("reclaiming ")[1].split(" ")[0])
    assert reclaimed > 4096


def test_preserves_message_ids_and_attachment_chips() -> None:
    conversation = _conversation(
        {
            "id": "u1",
            "role": "user",
            "content": "look at this",
            "attachments": [{"id": "a1", "name": "budget.pdf", "mime": "application/pdf"}],
        },
        {"id": "u2", "role": "user", "content": [_inlined_pdf()]},
    )

    _run()

    conversation.refresh_from_db()
    first, second = conversation.messages
    assert first["id"] == "u1"
    assert first["attachments"] == [{"id": "a1", "name": "budget.pdf", "mime": "application/pdf"}]
    assert second["id"] == "u2"


def test_leaves_a_conversation_with_no_inlined_bytes_untouched() -> None:
    conversation = _conversation({"id": "u1", "role": "user", "content": "book a flight"})

    output = _run()

    conversation.refresh_from_db()
    assert conversation.messages == [{"id": "u1", "role": "user", "content": "book a flight"}]
    assert "Rewrote 0 conversation(s), reclaiming 0 byte(s)." in output


def test_a_dry_run_reports_and_writes_nothing() -> None:
    conversation = _conversation({"id": "u1", "role": "user", "content": [_inlined_pdf()]})

    output = _run("--dry-run")

    conversation.refresh_from_db()
    assert conversation.messages[0]["content"][0]["type"] == "document"
    assert output.startswith("Would rewrite 1 conversation(s), reclaiming ")
