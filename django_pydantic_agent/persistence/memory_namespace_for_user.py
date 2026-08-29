from __future__ import annotations

from typing import Any

from django_pydantic_agent.persistence.utils import safe_namespace_segment

_USER_PREFIX = "u-"

# One shared segment for every unauthenticated caller. Unlike the request-bound
# resolver there is no session to key on here, so this cannot be a per-visitor
# bucket and does not pretend to be.
_ANONYMOUS = "anon"


def memory_namespace_for_user(user: Any) -> str:
    """The ``namespace`` for ``pydantic_ai_harness.memory.Memory``, from the acting user.

    The resolver to use when the capability is constructed at **mount time**,
    which is where every transport takes it — ``AGUIServer(capabilities=...)``
    resolves its list once, and no request exists yet::

        Memory(store, namespace=lambda ctx: memory_namespace_for_user(ctx.deps.user))

    Reading the user off ``ctx.deps`` rather than closing over a request is what
    lets one agent, built once, serve every caller: pydantic-ai clones each
    capability per run and ``Memory`` re-resolves its scope in the clone.
    ``AgentDeps.user`` is set by the transport from the authenticated request, so
    it is server-resolved and not something a client can choose.

    Use [`memory_namespace`][django_pydantic_agent.memory_namespace] instead when
    the store really is built per request and you hold one: it can key an
    anonymous caller to their browser session, which this cannot. With no request
    there is no session, so **every unauthenticated caller shares one namespace**
    — a real limitation rather than an oversight, and rarely reached, since a
    transport that serves anonymous callers at all has opted into it deliberately.

    The result is always a valid harness path segment: a segment-safe primary key
    is carried through readably behind a ``u-`` prefix, and anything else — an
    email-address primary key, a natural key with a slash, a pathological
    200-character pk that no longer fits once prefixed — is replaced by a digest.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return _ANONYMOUS
    return safe_namespace_segment(_USER_PREFIX, str(user.pk))


__all__ = ["memory_namespace_for_user"]
