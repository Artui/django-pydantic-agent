from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment


class NullAttachmentStore:
    """The default attachment store: uploads disabled, server stays stateless.

    A transport's attachments view detects this store and answers ``410 Gone``,
    so a misconfigured client gets a clear "uploads are off" signal rather than a
    silent success, and ``save`` is never reached. Called directly it raises,
    rather than fabricating a ref. ``open`` returns ``None`` so every fetch is a
    404, and ``delete`` is a no-op: the endpoint is inert until a real store is
    configured.
    """

    async def save(self, upload: UploadedFile, *, request: HttpRequest) -> AttachmentRef:
        raise NotImplementedError(
            "attachments are disabled: configure an attachment store to enable uploads"
        )

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        return None

    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None:
        return None


__all__ = ["NullAttachmentStore"]
