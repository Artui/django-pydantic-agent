from __future__ import annotations

from django.conf import settings
from django.core.files.storage import Storage, default_storage, storages

from django_pydantic_agent.constants import ATTACHMENT_STORAGE_ALIAS


def attachment_storage() -> Storage:
    """The ``Storage`` attachment bytes live in.

    A project naming a backend under
    [`ATTACHMENT_STORAGE_ALIAS`][django_pydantic_agent.constants.ATTACHMENT_STORAGE_ALIAS]
    in ``STORAGES`` gets that one; everyone else gets ``default_storage``, which
    is what the field held before this hook existed. It is returned as the same
    lazy object Django itself hands out, not a resolved instance: Django rebinds
    it whenever storage-related settings change, and a resolved copy would keep
    pointing at the backend that was configured when this app's models were
    imported -- writing attachments somewhere nothing else reads, for the life of
    the process.

    **The alias branch does resolve**, and that asymmetry is deliberate rather
    than overlooked. Django offers no lazy handle for a named alias, and matching
    the default's behaviour would take a ``LazyObject`` subclass plus a
    ``setting_changed`` receiver of our own. Nothing rebinds these settings in a
    running server, so the exposure is a *test suite* that overrides storage
    settings while an alias is configured: after such an override the field goes
    on writing through the backend resolved at import while reads go to the new
    one. Configure the alias in settings and leave it alone, which is what a
    deployment does anyway.

    Membership is tested rather than the lookup being wrapped in ``except``. The
    handler raises the same ``InvalidStorageError`` for "no such alias" and "that
    backend does not import", so catching it would turn a typo in a private
    bucket's dotted path into a silent fall back to the public default -- the
    one outcome this hook exists to prevent.
    """
    if ATTACHMENT_STORAGE_ALIAS in settings.STORAGES:
        return storages[ATTACHMENT_STORAGE_ALIAS]
    return default_storage


__all__ = ["attachment_storage"]
