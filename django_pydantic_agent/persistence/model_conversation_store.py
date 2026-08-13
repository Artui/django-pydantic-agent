from __future__ import annotations

from abc import ABC, abstractmethod

from asgiref.sync import sync_to_async
from django.http import HttpRequest

from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import ConversationMetaList
from django_pydantic_agent.persistence.utils import resolve_owner_id


class ModelConversationStore(ABC):
    """Abstract base for a model-backed (or any sync) ``ConversationStore``.

    Provides the async wrapping and per-request owner scoping; a subclass
    implements the three *synchronous* row operations against its own Django
    model. Model-agnostic on purpose — the package ships no concrete model, so it
    forces no migration and consumers define the fields and the owner relation.

    ``allow_anonymous`` governs whether anonymous requests are served. ``False``
    refuses them rather than collapsing every anonymous visitor into one shared
    owner bucket where they could read and delete each other's data. It is a
    *store* policy, so two endpoints sharing a store necessarily agree on it.
    Pass it explicitly: this substrate reads no Django settings.

    Example::

        class MyStore(ModelConversationStore):
            def _fetch(self, thread_id, owner_id):
                row = MyConversation.objects.filter(
                    thread_id=thread_id, owner_id=owner_id,
                ).first()
                return None if row is None else Conversation(...)
            def _store(self, conversation, owner_id): ...
            def _remove(self, thread_id, owner_id): ...
    """

    # A class-level immutable default, so a subclass that overrides __init__ and
    # forgets super() fails closed — refusing anonymous requests — rather than
    # raising AttributeError at request time or defaulting open.
    _allow_anonymous: bool = False

    def __init__(self, *, allow_anonymous: bool = False) -> None:
        """A subclass overriding this must call ``super().__init__()``."""
        self._allow_anonymous: bool = allow_anonymous

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        return await sync_to_async(self._load)(thread_id, request)

    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None:
        await sync_to_async(self._save)(conversation, request)

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._delete)(thread_id, request)

    async def list(self, *, request: HttpRequest, limit: int | None = None) -> ConversationMetaList:
        return await sync_to_async(self._list_scoped)(request, limit)

    async def exists(self, thread_id: str, *, request: HttpRequest) -> bool:
        return await sync_to_async(self._exists_scoped)(thread_id, request)

    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._rename_scoped)(thread_id, title, request)

    # Owner resolution and the sync row op share one thread: ``resolve_owner_id``
    # may create a session row for the anonymous bucket, so it cannot run on the
    # event loop. ``AnonymousOperationError`` propagates to the view as a 403.
    def _load(self, thread_id: str, request: HttpRequest) -> Conversation | None:
        return self._fetch(
            thread_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    def _save(self, conversation: Conversation, request: HttpRequest) -> None:
        self._store(conversation, resolve_owner_id(request, allow_anonymous=self._allow_anonymous))

    def _delete(self, thread_id: str, request: HttpRequest) -> None:
        self._remove(thread_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous))

    def _list_scoped(self, request: HttpRequest, limit: int | None) -> ConversationMetaList:
        return self._list(resolve_owner_id(request, allow_anonymous=self._allow_anonymous), limit)

    def _exists_scoped(self, thread_id: str, request: HttpRequest) -> bool:
        return self._exists(
            thread_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    def _rename_scoped(self, thread_id: str, title: str, request: HttpRequest) -> None:
        self._rename(
            thread_id, title, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    @abstractmethod
    def _fetch(self, thread_id: str, owner_id: str | None) -> Conversation | None: ...

    @abstractmethod
    def _store(self, conversation: Conversation, owner_id: str | None) -> None: ...

    @abstractmethod
    def _remove(self, thread_id: str, owner_id: str | None) -> None: ...

    def _list(self, owner_id: str | None, limit: int | None) -> ConversationMetaList:
        """Owner-scoped thread metadata for the drawer. Override to enable it.

        Concrete rather than abstract, so listing is opt-in. An override should
        answer from ``title`` / ``updated_at`` columns in one query, loading no
        message bodies, and apply a non-``None`` ``limit`` as a queryset slice so
        the cap reaches the DB.
        """
        return []

    def _exists(self, thread_id: str, owner_id: str | None) -> bool:
        """Owner-scoped presence check, probing via ``_fetch`` by default.

        That default loads the row, message body included, so a subclass with a
        metadata table should override it with an ``.exists()`` query.
        """
        return self._fetch(thread_id, owner_id) is not None

    def _rename(self, thread_id: str, title: str, owner_id: str | None) -> None:
        """Persist a renamed display title. A no-op unless overridden, paired
        with a ``title`` column the ``_list`` override reads back."""
        return None


__all__ = ["ModelConversationStore"]
