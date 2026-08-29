from __future__ import annotations

from django.http import HttpRequest

from django_pydantic_agent.persistence.utils import safe_namespace_segment

_USER_PREFIX = "u-"
_ANONYMOUS_PREFIX = "anon-"


def memory_namespace(request: HttpRequest) -> str:
    """The per-user ``namespace`` for ``pydantic_ai_harness.memory.Memory``, from a request.

    For a host that builds the capability per request and therefore holds one::

        Memory(store, namespace=lambda ctx: memory_namespace(request))

    Most transports do not: ``AGUIServer(capabilities=...)`` resolves its list
    once at mount time, where no request exists. Reach for
    [`memory_namespace_for_user`][django_pydantic_agent.memory_namespace_for_user]
    there. The one thing this resolver can do that the other cannot is key an
    anonymous caller to their **browser session**, so anonymous visitors get
    separate namespaces instead of sharing one.

    **Not `resolve_owner_id`, and the difference is load-bearing.** That helper
    returns ``anon:<session_key>`` for an anonymous request, and a colon is not in
    the alphabet the harness accepts for a path segment -- so ``Memory`` raises
    ``ValueError: invalid memory path`` for every anonymous visitor. That raise
    happens in the capability's ``for_run``, which is *outside* the store read
    that ``injection_errors`` guards, so the harness's own ``"ignore"`` default
    does not catch it and the whole run aborts. Hence a separate resolver whose
    only contract is that its result is always a valid segment.

    An identifier that is already segment-safe is used as-is behind a prefix; one
    that is not is replaced by a digest of it rather than sanitised, because
    stripping the offending characters maps ``tenant/42`` and ``tenant-42`` onto
    one namespace.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return safe_namespace_segment(_USER_PREFIX, str(user.pk))
    session = request.session
    if session.session_key is None:
        session.create()
    return safe_namespace_segment(_ANONYMOUS_PREFIX, str(session.session_key))


__all__ = ["memory_namespace"]
