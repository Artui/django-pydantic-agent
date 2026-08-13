from __future__ import annotations

from typing import Any, cast

from django.http import HttpRequest
from pydantic_ai.messages import BinaryContent, ToolReturn
from pydantic_ai.toolsets.function import FunctionToolset

from django_pydantic_agent.agent.types.attachment_inline_config import AttachmentInlineConfig
from django_pydantic_agent.persistence.types.attachment_store import AttachmentStore
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment

# Non-``text/*`` content types whose bytes are still UTF-8 text worth inlining.
_TEXTUAL_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "image/svg+xml",
    }
)


def build_attachment_toolset(
    store: AttachmentStore,
    request: HttpRequest,
    *,
    inline: AttachmentInlineConfig | None = None,
) -> FunctionToolset[None]:
    """Build a per-request toolset exposing ``read_attachment`` over ``store``.

    The request is captured in a closure so ``store.open`` stays owner-scoped to
    the acting user: the model can only read files that user uploaded, never
    another's by id. Attachments therefore travel the wire as lightweight refs
    and the bytes are reached here, server-side, only when the model asks.

    ``inline`` decides which binary types come back as file content and how large
    a file may be before it is described rather than attached; ``None`` takes the
    [`AttachmentInlineConfig`][django_pydantic_agent.AttachmentInlineConfig]
    defaults. Read its docstring before widening either — inlined bytes go to the
    provider on every model request left in the run.
    """
    resolved = AttachmentInlineConfig() if inline is None else inline

    async def read_attachment(attachment_id: str) -> str | ToolReturn:
        """Read a file the user attached to this conversation.

        Pass the ``id`` from an attachment chip on the user's message. A textual
        file comes back as its text. A binary file whose type can be read —
        a PDF or an image — comes back as attached file content, so read it
        directly rather than asking the user to send it again. A file that is
        too large, or of a type that cannot be read, comes back as a short note
        giving its name, type and size.
        """
        opened = await store.open(attachment_id, request=request)
        if opened is None:
            return f"No attachment with id {attachment_id!r} is available."
        return _render(opened, resolved)

    # ``read_attachment`` takes no ``RunContext``, and pydantic-ai's
    # ``FunctionToolset`` overloads only type the ctx-taking form.
    return FunctionToolset([cast("Any", read_attachment)], id="django-pydantic-agent-attachments")


def _render(opened: OpenedAttachment, inline: AttachmentInlineConfig) -> str | ToolReturn:
    """Decoded text, else the bytes as file content, else a one-line manifest."""
    ref = opened.ref
    # Read under a ``with`` so the streaming handle is always closed.
    with opened.content as handle:
        data = handle.read()
    text = _as_text(data, ref.mime)
    if text is not None:
        return text
    if ref.mime in inline.media_types:
        # Cap against the bytes in hand, not ``ref.size``: a store's declared
        # size need not match what it hands back, and only bytes reach the
        # provider.
        if len(data) <= inline.max_bytes:
            return ToolReturn(
                return_value=(
                    f"[{ref.name}] is a {ref.mime} file ({len(data)} bytes); "
                    "its contents are attached below."
                ),
                content=[BinaryContent(data=data, media_type=ref.mime)],
            )
        return (
            f"[{ref.name}] is a {ref.mime} file ({len(data)} bytes), over the "
            f"{inline.max_bytes}-byte limit for attaching a file's contents; "
            "it was described rather than attached."
        )
    return (
        f"[{ref.name}] is a {ref.mime or 'binary'} file ({ref.size} bytes); "
        "its content is not text and was not inlined."
    )


def _as_text(content: bytes, mime: str) -> str | None:
    """UTF-8 text for a textual content type, or ``None`` when binary/undecodable."""
    if not (mime.startswith("text/") or mime in _TEXTUAL_MIMES):
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


__all__ = ["build_attachment_toolset"]
