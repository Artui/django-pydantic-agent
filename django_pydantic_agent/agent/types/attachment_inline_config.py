from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentInlineConfig:
    """Which attachment types ``read_attachment`` hands back as file content.

    Tunes :func:`~django_pydantic_agent.agent.attachment_toolset.build_attachment_toolset`.
    A textual attachment is decoded and returned as text regardless of this
    record; it only governs the binary ones, where the choice is between
    attaching the bytes for the model to look at and returning a one-line note
    about a file it will never see.

    **An allowlist, not "everything that is not text".** Bytes the provider
    cannot interpret are not merely useless to the model — a ``.zip`` or a
    ``.exe`` handed over as file content makes the provider reject the whole
    request, so a broad rule would trade a model that cannot read your PDF for
    a run that does not start. The default set is what mainstream providers
    actually accept: PDF, PNG, JPEG, GIF and WebP.

    **``max_bytes`` is deliberately far below any sane upload cap**, because an
    inlined file is not paid for once. The bytes are carried in a synthetic
    ``user`` message that the tool return serialises into, and that message is
    persisted into the stored conversation, shipped to the browser on every
    thread load, and re-sent by the client on every following turn. Base64
    costs about a third on top: a 2300-byte PDF returned this way was measured
    at 3293 bytes in the stored thread, so a 4 MiB PDF costs roughly 5.5 MiB in
    the conversation row. That round trip is not waste — it is what lets a
    *follow-up* question about the same file be answered without a second
    ``read_attachment`` call — but it is the reason the cap sits where the cost
    stops being free rather than where the upload endpoint stops accepting
    files.

    To switch inlining off entirely and keep today's notes,
    ``AttachmentInlineConfig(media_types=frozenset())``.

    ``AttachmentRef.mime`` is client-declared, so the decision to inline is made
    on a hint rather than on sniffed bytes. The failure mode of a mislabelled
    file is a provider rejecting the request, not a disclosure: the store is
    owner-scoped either way, so the model only ever reaches files the acting
    user uploaded.
    """

    media_types: frozenset[str] = frozenset(
        {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
        }
    )
    """Content types whose bytes are attached to the tool result. Anything
    outside this set falls back to the one-line note."""

    max_bytes: int = 4 * 1024 * 1024
    """Largest file, in bytes, that is attached rather than described. Measured
    against the bytes the store returns, not the declared ``AttachmentRef.size``."""


__all__ = ["AttachmentInlineConfig"]
