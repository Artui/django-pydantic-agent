from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment


@runtime_checkable
class AttachmentStore(Protocol):
    """Pluggable server-side storage for files a user attaches to a conversation.

    Handed to a transport. The package ships
    :class:`~django_pydantic_agent.NullAttachmentStore` (uploads off) and the
    abstract :class:`~django_pydantic_agent.ModelAttachmentStore`; the opt-in
    ``django_pydantic_agent.contrib.store`` app adds a ready
    ``DefaultAttachmentStore`` keeping bytes in Django ``Storage`` and metadata
    in a row.

    Every method is async and **owner-scoped**: a store filters by the acting
    user so one user can never read or delete another's files, the security
    boundary for the whole feature. ``save`` validates nothing about size or type
    — the view does that from its own config — and just persists the bytes,
    returning a durable :class:`AttachmentRef`. ``open`` returns ``None`` for a
    missing *or cross-owner* id rather than raising, so a caller maps both to a
    404 and the two stay indistinguishable.

    Attachments need no scoped wrapper of the kind conversations have: they are
    id-referenced with no enumeration and already owner-scoped, so two endpoints
    sharing a store expose nothing across the user boundary. Thread lists are the
    case that leaks.
    """

    async def save(self, upload: UploadedFile, *, request: HttpRequest) -> AttachmentRef: ...
    async def open(
        self, attachment_id: str, *, request: HttpRequest
    ) -> OpenedAttachment | None: ...
    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None: ...


__all__ = ["AttachmentStore"]
