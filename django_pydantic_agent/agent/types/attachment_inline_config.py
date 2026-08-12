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

    **``max_bytes`` is deliberately far below any sane upload cap**, and what it
    guards is the cost of a *request*, not of a stored thread. The bytes are
    carried in a synthetic ``user`` message that the tool return serialises into,
    and that message stays in the run's history: every further model request in
    the same run ships the file again. Base64 costs about a third on top, so a
    4 MiB PDF is roughly 5.5 MiB of provider payload per request — paid in
    tokens, in latency, and in the memory the run holds it in.

    What it does **not** cost is the conversation. The bytes never travel on the
    event stream, so the client never receives them and the history it posts on
    the next turn carries none; whether they outlive the run at all is the
    transport's decision, and the AG-UI transport strips them on the way to
    storage. A *follow-up* question about the same file is therefore answered by
    reading the attachment again, server-side, rather than from bytes replayed
    out of the thread.

    So the cap sits where a single request stops being cheap, rather than where
    the upload endpoint stops accepting files.

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
