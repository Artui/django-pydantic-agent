from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentInlineConfig:
    """Which attachment types ``read_attachment`` hands back as file content.

    Tunes ``build_attachment_toolset``.
    ``media_types`` governs the **binary** attachments, where the choice is
    between attaching the bytes for the model to look at and returning a
    one-line note about a file it will never see;
    ``AttachmentInlineConfig(media_types=frozenset())`` switches inlining off
    entirely. A textual attachment is decoded and returned as text without
    consulting that allowlist.

    ``max_bytes`` governs **both**. A decoded text file reaches the provider and
    stays in the run's history exactly as inlined bytes do -- decoding changes
    the encoding, not the cost -- so a file over the limit is described rather
    than returned whichever branch it takes.

    ``media_types`` is an allowlist rather than "everything that is not text"
    because bytes a provider cannot interpret make it reject the whole request:
    a broad rule would trade a model that cannot read your PDF for a run that
    does not start. ``max_bytes`` sits far below any sane upload cap because what
    it bounds is one *request*, not a stored thread — the bytes ride in a
    synthetic ``user`` message that stays in the run's history, so every further
    model request ships the file again, base64 and all.

    None of it reaches the client: the bytes never travel on the event stream,
    and a follow-up question about the same file is answered by reading the
    attachment again, server-side. The decision is made on
    ``AttachmentRef.mime``, which is client-declared, so a mislabelled file costs
    a rejected request rather than a disclosure — the store is owner-scoped
    either way, and the model only reaches files the acting user uploaded.
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
    """Content types whose bytes are attached to the tool result; anything
    outside the set falls back to the one-line note."""

    max_bytes: int = 4 * 1024 * 1024
    """Largest file, in bytes, returned rather than described -- text and binary
    alike. Measured against the bytes the store returns, not the declared
    ``AttachmentRef.size``."""


__all__ = ["AttachmentInlineConfig"]
