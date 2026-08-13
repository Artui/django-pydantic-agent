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

    An authenticated request resolves to the user's pk. An anonymous one either
    raises
    [`AnonymousOperationError`][django_pydantic_agent.AnonymousOperationError],
    or, with ``allow_anonymous``, gets a per-browser ``anon:<session_key>``
    bucket, creating the session if the browser has none.

    Call this **inside** a sync context — the model stores wrap it in
    ``sync_to_async`` — because the anonymous branch may write a session row.
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
    """A thread title: the first user message, collapsed and truncated.

    Falls back to a generic label when there is no user text yet. A store that
    records an explicit rename uses that instead of calling this.
    """
    for message in messages:
        if message_field(message, "role") == "user":
            text = _clean(message_field(message, "content"))
            if text:
                return _truncate(text, _TITLE_LIMIT)
    return _DEFAULT_TITLE


def derive_preview(messages: list[Any]) -> str:
    """A one-line preview: the latest message with text, collapsed + truncated."""
    for message in reversed(messages):
        text = _clean(message_field(message, "content"))
        if text:
            return _truncate(text, _PREVIEW_LIMIT)
    return ""


def message_field(message: Any, name: str) -> Any:
    """Read one field off a message record: mapping key or attribute.

    The stored shape is JSON, but a transport may hand objects through, so both
    work. Total rather than raising — a missing field, or a record that is
    neither, answers ``None``, because callers read transport-owned shapes.
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
    "message_field",
    "owner_id_for",
    "resolve_owner_id",
]
