from __future__ import annotations

from typing import Any

import pytest

from django_pydantic_agent.contrib.store.strip_inline_binary_parts import strip_inline_binary_parts


def _document_part(value: str = "UERGQllURVM=") -> dict[str, Any]:
    return {
        "type": "document",
        "source": {"type": "data", "value": value, "mime_type": "application/pdf"},
        "metadata": None,
    }


def test_removes_an_inlined_document() -> None:
    messages = [{"id": "u1", "role": "user", "content": [_document_part()]}]

    stripped, changed = strip_inline_binary_parts(messages)

    assert changed is True
    assert stripped == [{"id": "u1", "role": "user", "content": []}]


def test_preserves_ids_and_non_standard_fields() -> None:
    messages = [
        {
            "id": "u1",
            "role": "user",
            "content": [{"type": "text", "text": "look at this"}, _document_part()],
            "attachments": [{"id": "a1", "name": "budget.pdf"}],
            "name": None,
        }
    ]

    stripped, changed = strip_inline_binary_parts(messages)

    assert changed is True
    assert stripped == [
        {
            "id": "u1",
            "role": "user",
            "content": [{"type": "text", "text": "look at this"}],
            "attachments": [{"id": "a1", "name": "budget.pdf"}],
            "name": None,
        }
    ]


@pytest.mark.parametrize(
    "part",
    [
        pytest.param({"type": "text", "text": "hello"}, id="text-part"),
        pytest.param(
            {"type": "document", "source": {"type": "url", "value": "https://example.test/a.pdf"}},
            id="document-fetched-by-url",
        ),
        pytest.param({"type": "document"}, id="document-with-no-source"),
        pytest.param({"type": "document", "source": "data"}, id="source-is-not-a-record"),
        pytest.param("a bare string part", id="part-is-not-a-record"),
    ],
)
def test_keeps_a_part_that_carries_no_bytes(part: Any) -> None:
    messages = [{"id": "u1", "role": "user", "content": [part]}]

    stripped, changed = strip_inline_binary_parts(messages)

    assert changed is False
    assert stripped == messages


@pytest.mark.parametrize("kind", ["audio", "document", "image", "video"])
def test_removes_every_kind_of_inlined_file(kind: str) -> None:
    messages = [{"id": "u1", "content": [{**_document_part(), "type": kind}]}]

    _, changed = strip_inline_binary_parts(messages)

    assert changed is True


def test_leaves_a_plain_string_message_alone() -> None:
    messages = [{"id": "u1", "role": "user", "content": "book a flight"}]

    stripped, changed = strip_inline_binary_parts(messages)

    assert changed is False
    assert stripped == messages


def test_leaves_a_message_that_is_not_a_record_alone() -> None:
    stripped, changed = strip_inline_binary_parts(["not a message"])

    assert changed is False
    assert stripped == ["not a message"]


def test_leaves_a_messages_value_that_is_not_a_list_alone() -> None:
    stripped, changed = strip_inline_binary_parts({"messages": []})

    assert changed is False
    assert stripped == {"messages": []}
