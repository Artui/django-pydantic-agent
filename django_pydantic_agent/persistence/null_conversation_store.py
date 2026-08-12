from __future__ import annotations

from django.http import HttpRequest

from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import ConversationMetaList


class NullConversationStore:
    """The default store: no-op, keeping the server stateless.

    ``load`` returns ``None`` and ``save`` / ``delete`` do nothing, so the
    conversation lives entirely in the client's posted history. A transport
    treats this store as "persistence off".
    """

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        return None

    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None:
        return None

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        return None

    async def list(self, *, request: HttpRequest, limit: int | None = None) -> ConversationMetaList:
        return []

    async def exists(self, thread_id: str, *, request: HttpRequest) -> bool:
        return False

    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None:
        return None


__all__ = ["NullConversationStore"]
