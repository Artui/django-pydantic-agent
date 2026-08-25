"""Which ``Storage`` attachment bytes are written to.

Agent uploads are user-supplied files a project usually wants somewhere private,
and the field used to bind to the global default -- so the only way to move them
was to move every other ``FileField`` in the project with them.
"""

from __future__ import annotations

from django.core.files.storage import InMemoryStorage, storages
from django.test import override_settings

from django_pydantic_agent.contrib.store.storage import (
    ATTACHMENT_STORAGE_ALIAS,
    attachment_storage,
)


def test_falls_back_to_the_project_default() -> None:
    # What every project that has not asked for anything gets, which is why the
    # default behaviour is unchanged.
    assert attachment_storage() is storages["default"]


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        ATTACHMENT_STORAGE_ALIAS: {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
)
def test_a_configured_alias_wins() -> None:
    resolved = attachment_storage()
    assert isinstance(resolved, InMemoryStorage)
    # The point of the alias: it is *not* the global default any more.
    assert resolved is not storages["default"]
