from __future__ import annotations


class AnonymousOperationError(Exception):
    """Raised when a model-backed store is asked to act for an anonymous request.

    The reference stores refuse anonymous thread / attachment operations unless
    they are constructed with ``allow_anonymous=True`` — otherwise every
    anonymous visitor would share one empty-string owner bucket and could read
    or delete each other's data. A transport's persistence views catch this and
    return ``403``.
    """


__all__ = ["AnonymousOperationError"]
