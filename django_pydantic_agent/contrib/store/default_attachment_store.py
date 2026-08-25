from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import uuid4

from django.core.files.uploadedfile import UploadedFile

from django_pydantic_agent.contrib.store.models import StoredAttachment
from django_pydantic_agent.contrib.store.utils import delete_attachments, hash_file
from django_pydantic_agent.persistence.model_attachment_store import ModelAttachmentStore
from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment

logger = logging.getLogger(__name__)


def _live_blob_name(twins: Iterable[StoredAttachment]) -> str | None:
    """The first stored path these rows offer that storage can still produce.

    A row is not proof of its bytes. Storage loses a blob without the row going
    with it -- a lifecycle rule expiring a key, a dump restored beside an empty
    bucket, a backend migrated underneath -- and a row can also carry no path at
    all, from a write that failed after the INSERT.

    Every candidate is tried rather than only the oldest, because the rows here
    already disagree with each other. Once one upload has healed a dead blob by
    writing its own, the owner holds both a dead row and a live one for the same
    bytes; checking only the oldest would find the dead one every time and write
    a fresh copy on every upload thereafter -- dedup lost permanently for those
    bytes, and storage growing without bound, which is the opposite of what this
    guard is for. Distinct paths are tried in row order, so the check costs one
    ``exists`` in the ordinary case where the oldest is alive.
    """
    seen: set[str] = set()
    for twin in twins:
        name = twin.file.name
        if not name or name in seen:
            continue
        seen.add(name)
        if twin.file.storage.exists(name):
            return name
    return None


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
        twins = StoredAttachment.objects.filter(owner_id=owner, sha256=digest).order_by("pk")
        adopted = _live_blob_name(twins)
        if adopted is not None:
            # Assigning the stored name adopts the existing blob without touching
            # Storage: no second read, no second write.
            row.file = adopted
        else:
            # ``save=False`` writes the bytes through Storage but defers the row
            # INSERT to the single ``row.save()`` below.
            #
            # A twin row is not proof its blob survives. Bytes go missing without
            # the row going with them -- a lifecycle rule expiring a key, a dump
            # restored beside an empty bucket, a backend migrated underneath.
            # Adopting the name on the row's word alone writes nothing and hands
            # back a reference to a file that is not there, and because identical
            # bytes hash identically, the re-upload that would repair it matches
            # the same dead twin and fails the same way. One ``exists`` on the
            # backend this branch is about to write to anyway is what keeps the
            # store self-healing rather than accumulating unreadable rows.
            row.file.save(attachment_id, upload, save=False)
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
        try:
            content = row.file.open("rb")
        except (FileNotFoundError, ValueError):
            # A row whose bytes are gone is an attachment this caller cannot have,
            # which is what ``open`` already promises to report as ``None`` -- the
            # contract exists so a caller has one branch for "unavailable" and
            # cannot tell a missing file from another owner's. Raising here sends
            # every caller down a path none of them writes: the toolset has a
            # sentence ready for ``None`` and gets an opaque tool failure instead,
            # and a ``FileResponse`` view returns a 500 where it means 404. Logged
            # rather than swallowed, because a missing blob beside a live row is a
            # storage fault worth someone's attention even though this request can
            # do nothing with it.
            #
            # Two ways to have no readable bytes, and the contract draws no line
            # between them: storage has lost the blob (``FileNotFoundError``), or
            # the row never recorded a path for one (``ValueError``, from
            # ``FieldFile``'s own empty-file guard).
            #
            # The owner is deliberately absent from the message. An anonymous
            # store spells it ``anon:<session_key>``, which is the caller's live
            # session credential, and application logs are the wrong place to put
            # one. The attachment id finds the row on its own.
            logger.warning(
                "Attachment %s has no readable stored file at %r; reporting it as unavailable.",
                row.attachment_id,
                row.file.name,
            )
            return None
        return OpenedAttachment(
            ref=AttachmentRef(id=row.attachment_id, name=row.name, mime=row.mime, size=row.size),
            content=content,
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
