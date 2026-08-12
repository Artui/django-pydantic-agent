from __future__ import annotations


class AnonymousOperationError(Exception):
    """Raised when a model-backed store is asked to act for an anonymous request.

    The reference stores refuse anonymous operations unless constructed with
    ``allow_anonymous=True``, since otherwise every anonymous visitor would share
    one owner bucket and could read or delete the others' data. A transport's
    persistence views catch this and return 403.
    """


__all__ = ["AnonymousOperationError"]
