from __future__ import annotations

from uuid import uuid4

from django.core.files.uploadedfile import UploadedFile

from django_pydantic_agent.contrib.store.models import StoredAttachment
from django_pydantic_agent.contrib.store.utils import delete_attachments, hash_file
from django_pydantic_agent.persistence.model_attachment_store import ModelAttachmentStore
from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment


class DefaultAttachmentStore(ModelAttachmentStore):
    """A ready-to-use model-backed store over :class:`StoredAttachment`.

    The batteries-included durable file store: bytes live in Django ``Storage``
    (filesystem by default, S3/GCS via ``STORAGES``), metadata in a row. Enable
    it by adding ``"django_pydantic_agent.contrib.store"`` to ``INSTALLED_APPS``, running
    ``migrate``, and passing an instance to your transport's
    ``attachment_store=`` argument. For a bespoke schema, subclass
    :class:`ModelAttachmentStore`.

    Owner scoping: every query filters by the ``owner_id`` the
    ``ModelAttachmentStore`` base resolves — the authenticated user's pk, or a
    per-browser ``anon:<session_key>`` bucket when
    the store is built with ``allow_anonymous=True`` (otherwise anonymous requests are
    refused rather than sharing one ``""`` bucket) — so one user's id never
    resolves another's file. The unique ``(owner_id, attachment_id)`` constraint
    holds regardless. The public ``attachment_id`` is an opaque UUID, kept
    separate from the storage filename.

    Uploads are **deduplicated by content hash, within one owner**. The same file
    sent into five threads is written to storage once and pointed at five times;
    see :meth:`_save` for why the row is still new every time, and why the scope
    is one owner rather than the whole table.
    """

    def _save(self, upload: UploadedFile, owner_id: str | None) -> AttachmentRef:
        """Persist one upload, reusing an identical blob this owner already has.

        A new row every time, even on a hit. The row carries the user-visible
        ``name`` that the composer renders on the chip, so the same bytes sent
        again as ``contract-final.pdf`` must not come back wearing the name of
        the first upload. What is shared is the blob underneath: both rows point
        at one stored file, and the bytes go only when the last of them does.

        The dedup lookup is scoped to ``owner_id`` and must stay that way. It
        looks like a missed optimisation — deduplicating across the whole table
        would store one copy of a document shared by a hundred tenants — but a
        cross-owner hit is a disclosure: it proves another tenant holds a file
        whose bytes you already have, which is exactly how you confirm that a
        named party is a customer, or that a leaked document came from here.
        Storage is cheaper than that.
        """
        attachment_id = uuid4().hex
        # Django's ``UploadedFile`` already truncates ``name`` to 255 chars
        # (preserving the extension), so it fits the model's ``CharField`` limit
        # without a DB ``DataError``; ``_basename`` only strips any path.
        name = _basename(upload.name)
        mime = upload.content_type or ""
        size = upload.size or 0
        # Hashed in chunks, before the bytes are written: a large upload is
        # fingerprinted without being held in memory whole, and the handle is
        # rewound so Storage still receives the entire file.
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
            # Assigning the stored name adopts the existing blob without
            # touching Storage at all — no second read, no second write.
            row.file = twin.file.name
        row.save()
        return AttachmentRef(id=attachment_id, name=name, mime=mime, size=size)

    def _open(self, attachment_id: str, owner_id: str | None) -> OpenedAttachment | None:
        row = StoredAttachment.objects.filter(
            owner_id=owner_id or "", attachment_id=attachment_id
        ).first()
        if row is None:
            return None
        # Hand back the open storage handle rather than reading the whole file:
        # ``FileResponse`` (download) and the tool's ``with`` block both stream /
        # close it, so a large attachment never lands in memory whole.
        return OpenedAttachment(
            ref=AttachmentRef(id=row.attachment_id, name=row.name, mime=row.mime, size=row.size),
            content=row.file.open("rb"),
        )

    def _remove(self, attachment_id: str, owner_id: str | None) -> None:
        """Drop one attachment, keeping the blob if another row still shares it.

        Deduplication makes the unconditional delete this used to do wrong: two
        rows can point at one stored file, and removing the bytes with the first
        of them would leave the second resolving to nothing.
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
