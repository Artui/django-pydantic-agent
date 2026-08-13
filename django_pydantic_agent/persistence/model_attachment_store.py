from __future__ import annotations

from abc import ABC, abstractmethod

from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment
from django_pydantic_agent.persistence.utils import resolve_owner_id


class ModelAttachmentStore(ABC):
    """Abstract base for a model-backed (or any sync) ``AttachmentStore``.

    The attachment twin of
    [`ModelConversationStore`][django_pydantic_agent.ModelConversationStore]
    — same async wrapping, same per-request owner scoping, same
    ``allow_anonymous`` policy — over a subclass's own storage: a Django
    ``Storage`` for the bytes, a model row for the metadata. The opt-in
    ``django_pydantic_agent.contrib.store`` app supplies a concrete pair.

    Each ``_save`` / ``_open`` / ``_remove`` receives the resolved ``owner_id``
    (``None`` for anonymous) and **must** filter by it, so files never cross
    users.

    Example::

        class MyStore(ModelAttachmentStore):
            def _save(self, upload, owner_id):
                row = MyAttachment.objects.create(owner_id=owner_id or "", ...)
                row.file.save(row.attachment_id, upload, save=True)
                return AttachmentRef(id=row.attachment_id, name=..., mime=..., size=...)
            def _open(self, attachment_id, owner_id): ...
            def _remove(self, attachment_id, owner_id): ...
    """

    # A class-level immutable default, so a subclass that overrides __init__ and
    # forgets super() fails closed — refusing anonymous requests — rather than
    # raising AttributeError at request time or defaulting open.
    _allow_anonymous: bool = False

    def __init__(self, *, allow_anonymous: bool = False) -> None:
        """A subclass overriding this must call ``super().__init__()``."""
        self._allow_anonymous: bool = allow_anonymous

    async def save(self, upload: UploadedFile, *, request: HttpRequest) -> AttachmentRef:
        return await sync_to_async(self._save_scoped)(upload, request)

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        return await sync_to_async(self._open_scoped)(attachment_id, request)

    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._remove_scoped)(attachment_id, request)

    # Owner resolution and the sync op share one thread: ``resolve_owner_id`` may
    # create a session row for the anonymous bucket, so it cannot run on the
    # event loop. ``AnonymousOperationError`` propagates to the view as a 403.
    def _save_scoped(self, upload: UploadedFile, request: HttpRequest) -> AttachmentRef:
        return self._save(upload, resolve_owner_id(request, allow_anonymous=self._allow_anonymous))

    def _open_scoped(self, attachment_id: str, request: HttpRequest) -> OpenedAttachment | None:
        return self._open(
            attachment_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    def _remove_scoped(self, attachment_id: str, request: HttpRequest) -> None:
        self._remove(
            attachment_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    @abstractmethod
    def _save(self, upload: UploadedFile, owner_id: str | None) -> AttachmentRef: ...

    @abstractmethod
    def _open(self, attachment_id: str, owner_id: str | None) -> OpenedAttachment | None: ...

    @abstractmethod
    def _remove(self, attachment_id: str, owner_id: str | None) -> None: ...


__all__ = ["ModelAttachmentStore"]
