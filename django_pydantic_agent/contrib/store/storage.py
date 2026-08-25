from __future__ import annotations

from django.core.files.storage import Storage, storages
from django.core.files.storage.handler import InvalidStorageError

ATTACHMENT_STORAGE_ALIAS = "django_pydantic_agent_attachments"


def attachment_storage() -> Storage:
    """The ``Storage`` attachment bytes live in.

    Resolved through ``STORAGES`` under
    ``ATTACHMENT_STORAGE_ALIAS``, falling back to the project's ``default``
    when no such alias is configured -- which is every project that has not
    asked for anything, so the default behaviour is unchanged.

    It exists because the alternative is worse. Agent uploads are user-supplied
    files that a project usually wants in a private bucket, and without a hook
    the only way to move them is to change the *global* default, which moves
    every other ``FileField`` in the project with them.

    Declared as a callable rather than a ``Storage`` instance so the migration
    records the import path and asks for the backend by name. Django resolves it
    once, when the field is constructed, so the alias has to be configured
    before the app's models are imported -- settings, which is where it lives.
    """
    try:
        return storages[ATTACHMENT_STORAGE_ALIAS]
    except InvalidStorageError:
        return storages["default"]
