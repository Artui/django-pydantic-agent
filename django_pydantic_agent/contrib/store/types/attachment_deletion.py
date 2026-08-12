from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentDeletion:
    """What one attachment deletion pass removed.

    Three counts rather than one, because deduplication pulls them apart:
    deleting one of two rows sharing a file removes a row and no blob, so a
    command reporting only ``rows`` would claim space it did not reclaim.

    ``bytes_freed`` is summed from the declared ``size`` of the rows whose blob
    went, not from a fresh stat of the backend, so it estimates what a remote
    store gives back rather than measuring it.
    """

    rows: int
    blobs: int
    bytes_freed: int


__all__ = ["AttachmentDeletion"]
