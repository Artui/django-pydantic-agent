from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentDeletion:
    """What one attachment deletion pass removed.

    Three counts rather than one, because after deduplication they genuinely
    differ: ``rows`` is how many attachment records were deleted, ``blobs`` how
    many stored files that actually freed. Deleting one of two rows that share a
    file removes a row and no blob at all, so a command reporting only rows would
    claim to have reclaimed space it did not.

    ``bytes_freed`` is summed from the declared ``size`` of the rows whose blob
    was removed — the metadata, not a fresh stat of the storage backend, so it is
    an estimate of what a remote store gives back rather than a measurement.
    """

    rows: int
    blobs: int
    bytes_freed: int


__all__ = ["AttachmentDeletion"]
