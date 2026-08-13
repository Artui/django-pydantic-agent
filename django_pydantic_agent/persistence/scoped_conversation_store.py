from __future__ import annotations

import dataclasses

from django.http import HttpRequest

from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import ConversationMetaList
from django_pydantic_agent.persistence.types.conversation_store import ConversationStore


class ScopedConversationStore:
    """Partition another
    [`ConversationStore`][django_pydantic_agent.ConversationStore] by a scope
    name.

    Stores key threads by ``(owner_id, thread_id)``. Two AG-UI endpoints sharing
    one store therefore share one user's thread list: a conversation started at
    ``/internal/agent`` appears in ``/public/agent``'s history drawer and can be
    resumed there — under the *public* agent's model, tools and guard policy.

    Wrapping fixes that without a migration::

        internal = AGUIServer(
            registry,
            conversation_store=ScopedConversationStore(store, scope="internal"),
        )
        public = AGUIServer(
            registry,
            conversation_store=ScopedConversationStore(store, scope="public"),
        )

    The partition is a thread-id prefix, so this composes with any
    implementation, third-party ones included, where a ``scope`` column would
    mean a migration and a breaking change to the
    [`ConversationStore`][django_pydantic_agent.ConversationStore]
    protocol every custom store implements.

    **Opt in explicitly.** A transport does not wrap by itself: doing so from its
    namespace would silently orphan the whole thread history of an existing
    single-endpoint project the moment it set one.

    The scope is invisible on the wire. Thread ids are echoed back to the client
    unchanged; only the storage key carries the prefix.
    """

    def __init__(self, inner: ConversationStore, *, scope: str) -> None:
        self._inner = inner
        self._scope = scope

    def _key(self, thread_id: str) -> str:
        return f"{self._scope}:{thread_id}"

    def _unkey(self, thread_id: str) -> str:
        prefix = f"{self._scope}:"
        return thread_id[len(prefix) :] if thread_id.startswith(prefix) else thread_id

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        conversation = await self._inner.load(self._key(thread_id), request=request)
        if conversation is None:
            return None
        # Hand back the id the client asked for, not the storage key.
        return dataclasses.replace(conversation, thread_id=self._unkey(conversation.thread_id))

    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None:
        await self._inner.save(
            dataclasses.replace(conversation, thread_id=self._key(conversation.thread_id)),
            request=request,
        )

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        await self._inner.delete(self._key(thread_id), request=request)

    async def list(self, *, request: HttpRequest, limit: int | None = None) -> ConversationMetaList:
        """This scope's threads only, with storage keys translated back.

        ``limit`` is applied by the inner store *before* this filter, so a busy
        sibling scope can crowd out rows. A store needing exact per-scope paging
        should partition at the query rather than by wrapping.
        """
        prefix = f"{self._scope}:"
        return [
            dataclasses.replace(meta, thread_id=self._unkey(meta.thread_id))
            for meta in await self._inner.list(request=request, limit=limit)
            if meta.thread_id.startswith(prefix)
        ]

    async def exists(self, thread_id: str, *, request: HttpRequest) -> bool:
        return await self._inner.exists(self._key(thread_id), request=request)

    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None:
        await self._inner.rename(self._key(thread_id), title, request=request)


__all__ = ["ScopedConversationStore"]
