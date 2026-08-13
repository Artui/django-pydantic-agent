from __future__ import annotations

from uuid import uuid4

from django.core.files.uploadedfile import UploadedFile

from django_pydantic_agent.contrib.store.models import StoredAttachment
from django_pydantic_agent.contrib.store.utils import delete_attachments, hash_file
from django_pydantic_agent.persistence.model_attachment_store import ModelAttachmentStore
from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment


class DefaultAttachmentStore(ModelAttachmentStore):
    """A ready-to-use model-backed store over ``StoredAttachment``.

    Bytes live in Django ``Storage`` (filesystem by default, S3 or GCS through
    ``STORAGES``), metadata in a row. Add
    ``"django_pydantic_agent.contrib.store"`` to ``INSTALLED_APPS``, run
    ``migrate``, and pass an instance to your transport's ``attachment_store=``.
    For a bespoke schema, subclass
    [`ModelAttachmentStore`][django_pydantic_agent.ModelAttachmentStore]
    instead.

    Every query filters by the ``owner_id`` the base resolves, so one user's id
    never reaches another's file, and the public ``attachment_id`` is an opaque
    UUID kept separate from the storage filename.

    Uploads are **deduplicated by content hash, within one owner**: the same file
    sent into five threads is written to storage once and pointed at five times,
    while still getting a row of its own each time so it keeps the name the
    composer showed. The blob goes when the last row pointing at it does.
    """

    def _save(self, upload: UploadedFile, owner_id: str | None) -> AttachmentRef:
        """Persist one upload, reusing an identical blob this owner already has.

        The dedup lookup is scoped to ``owner_id`` and must stay that way. It
        reads as a missed optimisation, since one copy could serve a hundred
        tenants, but a cross-owner hit is a disclosure: it proves another tenant
        holds a file whose bytes you already have, which is how you confirm that
        a named party is a customer, or that a leaked document came from here.
        """
        attachment_id = uuid4().hex
        # ``UploadedFile`` has already truncated ``name`` to 255 chars, extension
        # preserved, so it fits the model's ``CharField`` without a ``DataError``;
        # ``_basename`` only strips any path.
        name = _basename(upload.name)
        mime = upload.content_type or ""
        size = upload.size or 0
        # Hashed in chunks before the bytes are written, so a large upload is
        # fingerprinted without being held in memory whole; the handle is rewound
        # so Storage still receives the entire file.
        digest = hash_file(upload)
        owner = owner_id or ""
        row = StoredAttachment(
            attachment_id=attachment_id,
            owner_id=owner,
            name=name,
            mime=mime,
            size=size,
            sha256=digest,
        )
        twin = StoredAttachment.objects.filter(owner_id=owner, sha256=digest).first()
        if twin is None:
            # ``save=False`` writes the bytes through Storage but defers the row
            # INSERT to the single ``row.save()`` below.
            row.file.save(attachment_id, upload, save=False)
        else:
            # Assigning the stored name adopts the existing blob without touching
            # Storage: no second read, no second write.
            row.file = twin.file.name
        row.save()
        return AttachmentRef(id=attachment_id, name=name, mime=mime, size=size)

    def _open(self, attachment_id: str, owner_id: str | None) -> OpenedAttachment | None:
        row = StoredAttachment.objects.filter(
            owner_id=owner_id or "", attachment_id=attachment_id
        ).first()
        if row is None:
            return None
        # Hand back the open storage handle rather than reading the file: both
        # ``FileResponse`` and the tool's ``with`` block stream and close it, so a
        # large attachment never lands in memory whole.
        return OpenedAttachment(
            ref=AttachmentRef(id=row.attachment_id, name=row.name, mime=row.mime, size=row.size),
            content=row.file.open("rb"),
        )

    def _remove(self, attachment_id: str, owner_id: str | None) -> None:
        """Drop one attachment, keeping the blob if another row still shares it.

        Deduplication rules out deleting the bytes unconditionally: two rows can
        point at one stored file, and taking it with the first would leave the
        second resolving to nothing.
        """
        row = StoredAttachment.objects.filter(
            owner_id=owner_id or "", attachment_id=attachment_id
        ).first()
        if row is None:
            return
        delete_attachments([row])


def _basename(name: str | None) -> str:
    """The trailing filename component, stripped of any path; never empty."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base or "attachment"


__all__ = ["DefaultAttachmentStore"]
