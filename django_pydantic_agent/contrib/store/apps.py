from __future__ import annotations

from django.apps import AppConfig


class StoreConfig(AppConfig):
    """Opt-in app providing the reference persistence models + stores.

    Add ``"django_pydantic_agent.contrib.store"`` to ``INSTALLED_APPS`` and run
    ``migrate`` to enable it, then pass the matching store instance to your
    transport (``conversation_store=`` / ``attachment_store=`` / ``step_store=``).
    The base package ships no model of its own, so projects that don't opt in get
    no migration.

    Backs
    :class:`~django_pydantic_agent.contrib.store.default_conversation_store.DefaultConversationStore`,
    :class:`~django_pydantic_agent.contrib.store.default_attachment_store.DefaultAttachmentStore`,
    and
    :class:`~django_pydantic_agent.contrib.store.default_step_store.DefaultStepStore`.
    """

    name = "django_pydantic_agent.contrib.store"
    label = "django_pydantic_agent_store"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "django-pydantic-agent stores"
