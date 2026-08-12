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
    """A ready-to-use model-backed store over :class:`StoredConversation`.

    The batteries-included durable store: cross-device, per-user history with a
    cheap thread list. Enable it by adding ``"django_pydantic_agent.contrib.store"`` to
    ``INSTALLED_APPS``, running ``migrate``, and passing an instance to your
    transport's ``conversation_store=`` argument. For a bespoke schema, subclass
    :class:`ModelConversationStore` instead.

    Owner scoping: every query filters by the ``owner_id`` the
    ``ModelConversationStore`` base resolves — the authenticated user's pk, or a
    per-browser ``anon:<session_key>`` bucket when
    the store is built with ``allow_anonymous=True`` (otherwise anonymous requests are
    refused rather than sharing one ``""`` bucket). The unique
    ``(owner_id, thread_id)`` constraint holds regardless. Titles are derived
    from the first user message at first save and then left alone except by
    :meth:`_rename`; ``preview`` re-derives on every save.

    Saving also reconciles which attachments the thread refers to, and deleting
    it drops the ones nothing else refers to. Attachments therefore have a
    lifetime tied to the conversations that quote them rather than living
    forever; uploads that were never sent belong to no conversation at all and
    are collected by the ``agent_store_prune_attachments`` command instead.
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
            # Preserve the title (derived-at-creation or renamed); refresh the
            # body + preview to the latest exchange. ``.update()`` bypasses the
            # ``auto_now`` field, so bump ``updated_at`` explicitly to keep the
            # drawer ordered by recency.
            StoredConversation.objects.filter(pk=row.pk).update(
                **defaults, updated_at=timezone.now()
            )
        # Reconciled from the messages just stored, not from ``row.messages``,
        # which is a save behind on the update path.
        reconcile_conversation_attachments(row, messages)

    def _remove(self, thread_id: str, owner_id: str | None) -> None:
        """Delete the thread, and with it the attachments nothing else holds.

        The attachment rows this conversation referred to are collected first,
        because the links go with the conversation row; whichever of them no
        other conversation still references is then deleted, blobs included.
        Anything a second thread quotes survives untouched.

        Deleting ``StoredConversation`` rows by some other route — a queryset
        ``delete()``, the admin, a raw cascade — skips this and leaves the
        attachments behind with no reference. They are not lost bytes forever:
        that is precisely what the ``agent_store_prune_attachments`` command
        collects.
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
        # A metadata-only presence check — no message body deserialized, unlike
        # the base's ``_fetch`` fallback.
        return StoredConversation.objects.filter(
            owner_id=owner_id or "", thread_id=thread_id
        ).exists()

    def _rename(self, thread_id: str, title: str, owner_id: str | None) -> None:
        StoredConversation.objects.filter(owner_id=owner_id or "", thread_id=thread_id).update(
            title=title
        )


__all__ = ["DefaultConversationStore"]
