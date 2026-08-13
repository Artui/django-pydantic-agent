from __future__ import annotations

from django.utils import timezone

from django_pydantic_agent.contrib.store.models import StoredAttachment, StoredConversation
from django_pydantic_agent.contrib.store.reconcile_conversation_attachments import (
    reconcile_conversation_attachments,
)
from django_pydantic_agent.contrib.store.utils import delete_attachments, unreferenced_attachments
from django_pydantic_agent.persistence.model_conversation_store import ModelConversationStore
from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import (
    ConversationMeta,
    ConversationMetaList,
)
from django_pydantic_agent.persistence.utils import derive_preview, derive_title


class DefaultConversationStore(ModelConversationStore):
    """A ready-to-use model-backed store over ``StoredConversation``.

    Cross-device, per-user history with a cheap thread list. Add
    ``"django_pydantic_agent.contrib.store"`` to ``INSTALLED_APPS``, run
    ``migrate``, and pass an instance to your transport's
    ``conversation_store=``. For a bespoke schema, subclass
    [`ModelConversationStore`][django_pydantic_agent.ModelConversationStore] instead.

    Every query filters by the ``owner_id`` the base resolves. A title is derived
    from the first user message at first save and then left alone except by a
    rename; the preview re-derives on every save.

    Saving also reconciles which attachments the thread refers to, and deleting
    it drops the ones nothing else refers to, so an attachment's lifetime is tied
    to the conversations quoting it. An upload that was never sent belongs to no
    conversation, and the ``agent_store_prune_attachments`` command collects
    those instead.
    """

    def _fetch(self, thread_id: str, owner_id: str | None) -> Conversation | None:
        row = StoredConversation.objects.filter(
            owner_id=owner_id or "", thread_id=thread_id
        ).first()
        if row is None:
            return None
        return Conversation(
            thread_id=row.thread_id,
            messages=list(row.messages),
            owner_id=row.owner_id or None,
        )

    def _store(self, conversation: Conversation, owner_id: str | None) -> None:
        messages = conversation.messages
        defaults = {
            "messages": list(messages),
            "preview": derive_preview(messages),
        }
        row, created = StoredConversation.objects.get_or_create(
            owner_id=owner_id or "",
            thread_id=conversation.thread_id,
            defaults={**defaults, "title": derive_title(messages)},
        )
        if not created:
            # ``.update()`` preserves the title but bypasses the ``auto_now``
            # field, so bump ``updated_at`` explicitly to keep the drawer ordered
            # by recency.
            StoredConversation.objects.filter(pk=row.pk).update(
                **defaults, updated_at=timezone.now()
            )
        # Reconcile from the messages just stored: ``row.messages`` is a save
        # behind on the update path.
        reconcile_conversation_attachments(row, messages)

    def _remove(self, thread_id: str, owner_id: str | None) -> None:
        """Delete the thread, and with it the attachments nothing else holds.

        The referenced attachment rows are collected *first*, because the links
        go with the conversation row; whichever no other conversation still
        references is then deleted, blobs included.

        Deleting rows by another route — a queryset ``delete()``, the admin —
        skips this and leaves the attachments unreferenced, which is what
        ``agent_store_prune_attachments`` collects.
        """
        rows = StoredConversation.objects.filter(owner_id=owner_id or "", thread_id=thread_id)
        referenced = list(
            StoredAttachment.objects.filter(conversations__in=rows)
            .distinct()
            .values_list("pk", flat=True)
        )
        rows.delete()
        delete_attachments(
            unreferenced_attachments(StoredAttachment.objects.filter(pk__in=referenced))
        )

    def _list(self, owner_id: str | None, limit: int | None) -> ConversationMetaList:
        rows = (
            StoredConversation.objects.filter(owner_id=owner_id or "")
            .order_by("-updated_at")
            .values("thread_id", "title", "preview", "updated_at", "owner_id")
        )
        if limit is not None:
            rows = rows[:limit]
        return [
            ConversationMeta(
                thread_id=row["thread_id"],
                title=row["title"],
                updated_at=row["updated_at"],
                preview=row["preview"],
                owner_id=row["owner_id"] or None,
            )
            for row in rows
        ]

    def _exists(self, thread_id: str, owner_id: str | None) -> bool:
        # Metadata-only, unlike the base's ``_fetch`` fallback: no message body
        # is deserialized.
        return StoredConversation.objects.filter(
            owner_id=owner_id or "", thread_id=thread_id
        ).exists()

    def _rename(self, thread_id: str, title: str, owner_id: str | None) -> None:
        StoredConversation.objects.filter(owner_id=owner_id or "", thread_id=thread_id).update(
            title=title
        )


__all__ = ["DefaultConversationStore"]
