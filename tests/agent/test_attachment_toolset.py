from __future__ import annotations

import io
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory
from pydantic_ai.messages import BinaryContent, ToolReturn

from django_pydantic_agent.agent.attachment_toolset import build_attachment_toolset
from django_pydantic_agent.agent.types.attachment_inline_config import AttachmentInlineConfig
from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment


class _FakeStore:
    def __init__(self, opened: OpenedAttachment | None) -> None:
        self.opened = opened
        self.opened_ids: list[str] = []

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        self.opened_ids.append(attachment_id)
        return self.opened


def _read_attachment(
    store: Any, request: HttpRequest, *, inline: AttachmentInlineConfig | None = None
) -> Any:
    toolset = build_attachment_toolset(store, request, inline=inline)
    assert toolset.id == "django-pydantic-agent-attachments"
    return toolset.tools["read_attachment"].function


def _opened(mime: str, content: bytes, *, name: str = "f", size: int = 3) -> OpenedAttachment:
    return OpenedAttachment(
        ref=AttachmentRef(id="a1", name=name, mime=mime, size=size), content=io.BytesIO(content)
    )


async def test_reads_textual_attachment_as_text() -> None:
    request = RequestFactory().get("/")
    store = _FakeStore(_opened("text/plain", b"hello world"))
    read = _read_attachment(store, request)
    assert await read("a1") == "hello world"
    assert store.opened_ids == ["a1"]


async def test_reads_non_text_mime_as_a_manifest() -> None:
    # A PNG the client declared as a generic byte stream: the type is a hint, and
    # a type outside the inline allowlist is described rather than attached.
    store = _FakeStore(_opened("application/octet-stream", b"\x89PNG\r\n", name="logo.png", size=6))
    read = _read_attachment(store, RequestFactory().get("/"))
    result = await read("a1")
    assert "logo.png" in result
    assert "application/octet-stream" in result
    assert "6 bytes" in result


async def test_textual_mime_but_undecodable_bytes_is_a_manifest() -> None:
    # ``text/plain`` content type, but the bytes aren't valid UTF-8.
    store = _FakeStore(_opened("text/plain", b"\xff\xfe", name="bad.txt", size=2))
    read = _read_attachment(store, RequestFactory().get("/"))
    result = await read("a1")
    assert "bad.txt" in result
    assert "not text" in result


async def test_missing_attachment_reports_clearly() -> None:
    store = _FakeStore(None)
    read = _read_attachment(store, RequestFactory().get("/"))
    assert await read("ghost") == "No attachment with id 'ghost' is available."


async def test_empty_mime_renders_as_binary() -> None:
    store = _FakeStore(_opened("", b"\x00\x01", name="blob", size=2))
    read = _read_attachment(store, RequestFactory().get("/"))
    assert "binary" in await read("a1")


async def test_inlineable_pdf_comes_back_as_binary_content() -> None:
    pdf = b"%PDF-1.4 budget"
    store = _FakeStore(_opened("application/pdf", pdf, name="budget.pdf", size=len(pdf)))
    read = _read_attachment(store, RequestFactory().get("/"), inline=AttachmentInlineConfig())
    result = await read("a1")
    assert isinstance(result, ToolReturn)
    assert "budget.pdf" in result.return_value
    assert result.content == [BinaryContent(data=pdf, media_type="application/pdf")]


async def test_inlineable_image_comes_back_as_binary_content() -> None:
    png = b"\x89PNG\r\n\x1a\n"
    store = _FakeStore(_opened("image/png", png, name="logo.png", size=len(png)))
    read = _read_attachment(store, RequestFactory().get("/"), inline=AttachmentInlineConfig())
    result = await read("a1")
    assert isinstance(result, ToolReturn)
    assert "logo.png" in result.return_value
    assert result.content == [BinaryContent(data=png, media_type="image/png")]


async def test_oversized_inlineable_file_falls_back_to_a_note() -> None:
    store = _FakeStore(_opened("application/pdf", b"%PDF-1.4", name="huge.pdf", size=8))
    read = _read_attachment(
        store, RequestFactory().get("/"), inline=AttachmentInlineConfig(max_bytes=1)
    )
    result = await read("a1")
    assert isinstance(result, str)
    assert "huge.pdf" in result
    # Names the limit, so the model can tell "too big" from "cannot be read".
    assert "1-byte limit" in result
    assert "not text" not in result


async def test_binary_type_outside_the_allowlist_is_not_inlined() -> None:
    store = _FakeStore(_opened("application/zip", b"PK\x03\x04", name="bundle.zip", size=4))
    read = _read_attachment(store, RequestFactory().get("/"), inline=AttachmentInlineConfig())
    assert await read("a1") == (
        "[bundle.zip] is a application/zip file (4 bytes); "
        "its content is not text and was not inlined."
    )


async def test_inlining_can_be_switched_off_entirely() -> None:
    store = _FakeStore(_opened("application/pdf", b"%PDF-1.4", name="budget.pdf", size=8))
    read = _read_attachment(
        store, RequestFactory().get("/"), inline=AttachmentInlineConfig(media_types=frozenset())
    )
    assert await read("a1") == (
        "[budget.pdf] is a application/pdf file (8 bytes); "
        "its content is not text and was not inlined."
    )


async def test_the_default_config_is_used_when_none_is_passed() -> None:
    store = _FakeStore(_opened("application/pdf", b"%PDF-1.4", name="budget.pdf", size=8))
    toolset = build_attachment_toolset(store, RequestFactory().get("/"))
    read = toolset.tools["read_attachment"].function
    assert isinstance(await read("a1"), ToolReturn)


async def test_a_zero_byte_inlineable_file_is_still_inlined() -> None:
    store = _FakeStore(_opened("application/pdf", b"", name="empty.pdf", size=0))
    read = _read_attachment(
        store, RequestFactory().get("/"), inline=AttachmentInlineConfig(max_bytes=0)
    )
    result = await read("a1")
    assert isinstance(result, ToolReturn)
    assert result.content == [BinaryContent(data=b"", media_type="application/pdf")]
