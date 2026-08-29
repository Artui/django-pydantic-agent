from __future__ import annotations

from django.apps import AppConfig


class StoreConfig(AppConfig):
    """Opt-in app providing the reference persistence models and stores.

    Add ``"django_pydantic_agent.contrib.store"`` to ``INSTALLED_APPS``, run
    ``migrate``, then pass the matching store to your transport. The base package
    ships no model of its own, so a project that does not opt in gets no
    migration. Backs ``DefaultConversationStore``, ``DefaultAttachmentStore``,
    ``DefaultStepStore`` and ``DefaultMemoryStore``.
    """

    name = "django_pydantic_agent.contrib.store"
    label = "django_pydantic_agent_store"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "django-pydantic-agent stores"
