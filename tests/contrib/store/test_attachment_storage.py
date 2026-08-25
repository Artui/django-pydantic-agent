"""Which ``Storage`` attachment bytes are written to.

Agent uploads are user-supplied files a project usually wants somewhere private,
and the field used to bind to the global default -- so the only way to move them
was to move every other ``FileField`` in the project with them.
"""

from __future__ import annotations

import pytest
from django.core.files.storage import (
    InMemoryStorage,
    InvalidStorageError,
    default_storage,
    storages,
)
from django.test import override_settings

from django_pydantic_agent.constants import ATTACHMENT_STORAGE_ALIAS
from django_pydantic_agent.contrib.store.attachment_storage import attachment_storage
from django_pydantic_agent.contrib.store.models import StoredAttachment


def test_falls_back_to_the_projects_lazy_default() -> None:
    # The *same* lazy object Django hands out, not a resolved copy of whatever it
    # pointed at when this app was imported. Django rebinds it when storage
    # settings change, and a copy would go on writing to the old backend.
    assert attachment_storage() is default_storage


def test_the_model_field_follows_the_default_through_a_settings_change() -> None:
    # The regression this shape exists to prevent: an ``override_settings`` for
    # any storage-related key -- ``STATIC_URL`` counts -- used to split the field
    # from ``default_storage`` permanently, for the life of the process.
    field = StoredAttachment._meta.get_field("file")
    with override_settings(STATIC_URL="/other-static/"):
        assert field.storage is default_storage
    assert field.storage is default_storage


def test_the_field_is_wired_to_this_hook() -> None:
    field = StoredAttachment._meta.get_field("file")
    assert field._storage_callable is attachment_storage


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


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        ATTACHMENT_STORAGE_ALIAS: {"BACKEND": "myproject.storages.NoSuchStorag"},
    }
)
def test_a_misspelled_backend_raises_rather_than_falling_back() -> None:
    # The handler raises the same error for "no such alias" and "that backend
    # does not import", so catching it would put user-supplied files in the
    # public default over a one-character typo -- the outcome the alias exists to
    # prevent. Membership is tested instead, so this surfaces.
    with pytest.raises(InvalidStorageError):
        attachment_storage()
