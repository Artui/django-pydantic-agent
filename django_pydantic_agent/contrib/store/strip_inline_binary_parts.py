from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The content-part types that can carry a file. A part of one of these types
# holds bytes only when its source is inline data; a source pointing at a URL
# costs the row nothing and is left alone.
_FILE_PART_TYPES = frozenset({"audio", "document", "image", "video"})


def strip_inline_binary_parts(messages: Any) -> tuple[Any, bool]:
    """Rebuild ``messages`` without any inline file bytes, and say if it changed.

    A transport that inlines an attachment for the model serialises the file into
    the message list as base64 — a synthetic user message whose ``content`` is a
    list of parts, one of them a document or image whose ``source`` carries the
    data itself. Persisted, that turns a 2.6 MB PDF into roughly 3.5 MB of text
    in a single row, shipped to the browser on every load of the thread.

    The edit is **structural**: the message list is walked as plain JSON and only
    the offending parts are dropped. Nothing is round-tripped through a message
    type, because validating and re-dumping is exactly what would silently
    discard the fields this must not lose — every message's ``id``, and the
    non-standard ``attachments`` array the composer rides on a user message,
    which is both what renders the chips and what the attachment relation is
    reconciled from.

    A message whose parts were all bytes keeps its place with an empty
    ``content`` list rather than being removed: dropping the message would take
    its id with it, and this command's job is to reclaim space, not to rewrite
    the transcript.

    Returns the rebuilt messages and whether anything was actually removed, so a
    caller can leave untouched rows untouched. A ``messages`` value that is not a
    list at all is handed straight back: the column is JSON, so a row written by
    something other than this store need only be left alone.
    """
    if not isinstance(messages, list):
        return messages, False
    rebuilt: list[Any] = []
    changed = False
    for message in messages:
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            rebuilt.append(message)
            continue
        kept = [part for part in content if not _is_inline_binary(part)]
        if len(kept) == len(content):
            rebuilt.append(message)
            continue
        changed = True
        rebuilt.append({**message, "content": kept})
    return rebuilt, changed


def _is_inline_binary(part: Any) -> bool:
    """True for a content part that carries file bytes inline."""
    if not isinstance(part, Mapping) or part.get("type") not in _FILE_PART_TYPES:
        return False
    source = part.get("source")
    return isinstance(source, Mapping) and source.get("type") == "data"


__all__ = ["strip_inline_binary_parts"]
