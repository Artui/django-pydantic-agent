from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import ConversationMetaList


@runtime_checkable
class ConversationStore(Protocol):
    """Pluggable server-side persistence for AG-UI conversations.

    Handed to a transport. The package ships ``NullConversationStore`` (the
    server stays stateless) and a session-backed implementation; projects supply
    their own. All methods are async so an implementation can use the async ORM
    or a network backend.

    Threads key by ``(owner_id, thread_id)``, so two endpoints sharing a store
    share one user's thread list. Wrap with
    [`ScopedConversationStore`][django_pydantic_agent.ScopedConversationStore]
    to partition them.

    ``list`` returns owner-scoped metadata only, no message bodies, capped at
    ``limit`` rows (``None`` for the store's own default); a store that cannot
    enumerate returns an empty list. ``exists`` is a presence check that loads no
    message body, so a rename or probe does not deserialize a whole thread just
    to 404. ``rename`` sets a display title, and is a no-op in a store that
    cannot persist one.
    """

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None: ...
    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None: ...
    async def delete(self, thread_id: str, *, request: HttpRequest) -> None: ...
    async def list(
        self, *, request: HttpRequest, limit: int | None = None
    ) -> ConversationMetaList: ...
    async def exists(self, thread_id: str, *, request: HttpRequest) -> bool: ...
    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None: ...


__all__ = ["ConversationStore"]
