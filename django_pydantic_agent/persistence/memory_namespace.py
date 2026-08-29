from __future__ import annotations

import hashlib
import re

from django.http import HttpRequest

# The shape `pydantic-ai-harness` validates every memory path segment against
# (`memory._store._VALID_SEGMENT_RE`), restated rather than imported: the harness
# is an optional extra, and a namespace has to be resolvable in a project that
# never installs it. Kept in step with upstream by
# ``tests/persistence/test_memory_namespace.py``, which checks the result against
# the real ``validate_store_path`` when the extra is present.
_SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_.-]{1,200}")

# Long enough that two distinct identifiers colliding is not a threat model, short
# enough to leave room under the 200-character segment limit for any prefix.
_HASH_CHARS = 40

_USER_PREFIX = "u-"
_ANONYMOUS_PREFIX = "anon-"


def memory_namespace(request: HttpRequest) -> str:
    """The per-user ``namespace`` for ``pydantic_ai_harness.memory.Memory``.

    Pass it as the capability's resolver, reading the request the view already
    holds::

        Memory(store, namespace=lambda ctx: memory_namespace(request))

    **Not `resolve_owner_id`, and the difference is load-bearing.** That helper
    returns ``anon:<session_key>`` for an anonymous request, and a colon is not
    in the alphabet the harness accepts for a path segment — so ``Memory`` raises
    ``ValueError: invalid memory path`` for every anonymous visitor. That raise
    happens in the capability's ``for_run``, which is *outside* the store read
    that ``injection_errors`` guards, so the harness's own ``"ignore"`` default
    does not catch it and the whole run aborts. Hence a separate resolver whose
    only contract is that its result is always a valid segment.

    An identifier that is already segment-safe is used as-is behind a prefix; one
    that is not — an email-address primary key, a natural key with a slash, a
    pathological 200-character pk that no longer fits once prefixed — is replaced
    by a digest of it. **Substituting a digest rather than stripping the offending
    characters is the point:** stripping maps ``a/b`` and ``a-b`` onto one
    namespace, and two users sharing a namespace is exactly the failure this
    resolver exists to prevent.

    The namespace is not a security boundary on its own, and is not asked to be:
    [`DefaultMemoryStore`][django_pydantic_agent.contrib.store.default_memory_store.DefaultMemoryStore]
    partitions rows by the owner it resolves server-side, so a namespace that is
    wrong, guessed, or contains a ``/`` that opens extra path segments still
    cannot read another owner's rows.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return _segment(_USER_PREFIX, str(user.pk))
    session = request.session
    if session.session_key is None:
        session.create()
    return _segment(_ANONYMOUS_PREFIX, str(session.session_key))


def _segment(prefix: str, identifier: str) -> str:
    """``prefix`` + ``identifier``, or + a digest of it when that is not a valid segment.

    The prefix is inside the check rather than bolted on after, because whether
    the result fits the 200-character limit depends on the prefix too.
    """
    candidate = f"{prefix}{identifier}"
    # ``..`` is rejected by the harness independently of the character class, so
    # a segment built from ``a..b`` matches the regex and is still refused.
    if _SAFE_SEGMENT_RE.fullmatch(candidate) and ".." not in candidate:
        return candidate
    return f"{prefix}{hashlib.sha256(identifier.encode()).hexdigest()[:_HASH_CHARS]}"


__all__ = ["memory_namespace"]
