from __future__ import annotations

from dataclasses import dataclass
from typing import IO

from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef


@dataclass(frozen=True)
class OpenedAttachment:
    """An attachment's metadata paired with a readable byte stream.

    Returned by ``AttachmentStore.open``, so a download view and the
    ``read_attachment`` tool both get the content *and* the
    [`AttachmentRef`][django_pydantic_agent.AttachmentRef] in one owner-scoped call.

    ``content`` is an open binary stream rather than the bytes, so a large
    attachment streams out instead of being buffered. **The consumer owns it and
    must read it exactly once** — hand it to ``FileResponse``, which closes it,
    or read it under a ``with`` block.
    """

    ref: AttachmentRef
    content: IO[bytes]


__all__ = ["OpenedAttachment"]
