from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentRef:
    """A durable, lightweight reference to one uploaded file.

    What an upload returns and what travels on the wire — never the bytes. The
    file is uploaded out of band, the client holds this ref on the message, and
    the agent reads the bytes server-side through the ``read_attachment`` tool.

    ``id`` is the opaque, owner-scoped handle the store resolves back to bytes.
    ``mime`` is client-declared, so treat it as a hint. ``url`` is an optional
    direct fetch URL, such as an owner-checked download endpoint, and stays
    ``None`` unless a store fills it in.
    """

    id: str
    name: str
    mime: str
    size: int
    url: str | None = None


__all__ = ["AttachmentRef"]
