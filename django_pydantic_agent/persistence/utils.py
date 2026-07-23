from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest

from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError

_TITLE_LIMIT = 60
_PREVIEW_LIMIT = 120
_DEFAULT_TITLE = "New conversation"


def owner_id_for(request: HttpRequest) -> str | None:
    """The authenticated user's id as a string, or ``None`` when anonymous."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user.pk)
    return None


def resolve_owner_id(request: HttpRequest, *, allow_anonymous: bool) -> str:
    """The owner-scoping id a model-backed store persists under (never ``None``).

    Authenticated → the user's pk. Anonymous → governed by ``allow_anonymous``,
    which the calling store holds (it is a store policy, not endpoint config —
    two endpoints sharing a store must agree on it):

    - **off** (default) → raise :class:`AnonymousOperationError`. Refusing beats
      the old behaviour of collapsing every anonymous visitor into one ``""``
      bucket, where they could read / delete each other's threads + attachments.
    - **on** → a per-browser bucket derived from the session key
      (``anon:<session_key>``), creating the session if the browser has none.

    Call this **inside** a sync context (the model stores wrap it in
    ``sync_to_async``): the anonymous branch may write a new session row.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user.pk)
    if not allow_anonymous:
        raise AnonymousOperationError(
            "Anonymous requests are refused by this store. Authenticate the "
            "request (your transport's require_authenticated / get_user hook), "
            "or construct the store with allow_anonymous=True to bucket "
            "anonymous users by browser session."
        )
    session = request.session
    if session.session_key is None:
        session.create()
    return f"anon:{session.session_key}"


def derive_title(messages: list[Any]) -> str:
    """A thread title: the first user message, collapsed + truncated.

    Falls back to a generic label when there is no user text yet (a brand-new
    or assistant-only thread). Stores that record an explicit rename use that
    instead of calling this.
    """
    for message in messages:
        if _field(message, "role") == "user":
            text = _clean(_field(message, "content"))
            if text:
                return _truncate(text, _TITLE_LIMIT)
    return _DEFAULT_TITLE


def derive_preview(messages: list[Any]) -> str:
    """A one-line preview: the latest message with text, collapsed + truncated."""
    for message in reversed(messages):
        text = _clean(_field(message, "content"))
        if text:
            return _truncate(text, _PREVIEW_LIMIT)
    return ""


def _field(message: Any, name: str) -> Any:
    """Read one field off a message record — mapping key or attribute.

    The stored shape is JSON (mappings), but a transport may hand objects
    straight through, so both are supported.
    """
    if isinstance(message, Mapping):
        return message.get(name)
    return getattr(message, name, None)


def _clean(content: Any) -> str:
    """Whitespace-collapsed message text, or ``""`` for non-string content."""
    if not isinstance(content, str):
        return ""
    return " ".join(content.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = [
    "derive_preview",
    "derive_title",
    "owner_id_for",
    "resolve_owner_id",
]
