from __future__ import annotations

import hashlib
from collections.abc import Iterable

from django.core.files.base import File
from django.db.models import QuerySet

from django_pydantic_agent.contrib.store.models import StoredAttachment
from django_pydantic_agent.contrib.store.types.attachment_deletion import AttachmentDeletion

# 64 KiB: Django's own default read size for a ``File``.
_CHUNK_SIZE = 64 * 1024


def hash_file(handle: File) -> str:
    """The SHA-256 of ``handle``'s bytes, as hex, read a chunk at a time.

    Chunked deliberately: fingerprinting a 10 MiB upload must not put 10 MiB in
    memory, and the same helper hashes files already in storage (where the bytes
    may be coming back over the network from S3).

    ``File.chunks()`` rewinds to the start when the underlying stream supports
    it, so a handle hashed here can still be handed to ``Storage.save``
    afterwards and will be written whole.
    """
    digest = hashlib.sha256()
    for chunk in handle.chunks(chunk_size=_CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def unreferenced_attachments(attachments: QuerySet[StoredAttachment]) -> QuerySet[StoredAttachment]:
    """Narrow ``attachments`` to those no conversation refers to.

    The single definition of "unreferenced", so the conversation cascade and the
    garbage-collection command cannot drift apart on what an orphan is. Note
    what it does *not* consider: an upload that was never sent is unreferenced
    from the moment it lands, which is why the command pairs this with an age
    threshold.
    """
    return attachments.filter(conversations__isnull=True)


def delete_attachments(attachments: Iterable[StoredAttachment]) -> AttachmentDeletion:
    """Delete each row, and its blob once no surviving row shares that blob.

    Deduplication means the row count and the blob count come apart: several
    rows may point at one stored file, each with its own user-visible name. The
    bytes therefore go only when the last row referring to them does, and the
    check is on the stored file name rather than on ``sha256`` — the name is what
    identifies a blob, while the hash is merely what caused two rows to share
    one. Rows hashed by a backfill can carry the same digest while pointing at
    two distinct files written before deduplication existed; deleting on the
    digest would leak one of them.
    """
    rows = 0
    blobs = 0
    freed = 0
    for attachment in attachments:
        stored_name = attachment.file.name
        size = attachment.size
        attachment.delete()
        rows += 1
        if not stored_name or StoredAttachment.objects.filter(file=stored_name).exists():
            continue
        attachment.file.delete(save=False)
        blobs += 1
        freed += size
    return AttachmentDeletion(rows=rows, blobs=blobs, bytes_freed=freed)


def preview_attachment_deletion(attachments: list[StoredAttachment]) -> AttachmentDeletion:
    """What :func:`delete_attachments` would remove, removing nothing.

    Kept next to the function it predicts because the two have to agree: a
    dry run that reports the row count and calls it "space reclaimed" overstates
    the result the moment two rows share a blob, and a dry run you cannot trust
    is worse than none on a command whose whole job is deleting things.

    A blob counts as freed when every row pointing at it is in ``attachments`` —
    so rows that merely share a file with each other still free it once, and one
    left behind outside the set keeps it.
    """
    sizes: dict[str, int] = {}
    for attachment in attachments:
        if attachment.file.name:
            sizes.setdefault(attachment.file.name, attachment.size)
    kept = set(
        StoredAttachment.objects.filter(file__in=list(sizes))
        .exclude(pk__in=[attachment.pk for attachment in attachments])
        .values_list("file", flat=True)
    )
    freed = [size for name, size in sizes.items() if name not in kept]
    return AttachmentDeletion(rows=len(attachments), blobs=len(freed), bytes_freed=sum(freed))


__all__ = [
    "delete_attachments",
    "hash_file",
    "preview_attachment_deletion",
    "unreferenced_attachments",
]
