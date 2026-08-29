from __future__ import annotations

import re
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.db import transaction
from django.http import HttpRequest
from pydantic_ai import ModelRetry
from pydantic_ai_harness.memory import (
    MemoryConflictError,
    MemoryFile,
    MemoryMutation,
    MemoryOperation,
    MemoryOperationConflictError,
)

from django_pydantic_agent.contrib.store.models import StoredMemory, StoredMemoryOperation
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.persistence.utils import resolve_owner_id

# The two literal fence tags the harness wraps injected memory in
# (``memory._capability._MEMORY_DATA_PREFIX`` / ``_SUFFIX``). Matched with
# optional internal whitespace and either slash position, because the reader
# being protected is a language model rather than an XML parser: ``< /MEMORY >``
# ends the block for it just as surely as the exact bytes do.
_FENCE_TAG_RE = re.compile(r"<\s*/?\s*memory\s*>", re.IGNORECASE)

# The column width for ``StoredMemory.path``. Every harness segment is capped at
# 200 characters but the joined path is not capped at all, so the store states
# its own limit rather than letting the database truncate or reject one.
_MAX_PATH_CHARS = 500

_DEFAULT_MAX_FILES = 64
_DEFAULT_MAX_TOTAL_CHARS = 262_144


class DefaultMemoryStore:
    """A durable, owner-scoped ``MemoryStore`` over the reference models.

    The database equivalent of the harness's own ``SqliteMemoryStore`` /
    ``FileStore``. It structurally satisfies ``pydantic-ai-harness``'s
    ``MemoryStore`` protocol — that protocol is upstream's, not this package's —
    while partitioning every row by the resolved owner, so one user's memory can
    never be read or overwritten by another even when the namespace handed to the
    capability is wrong. Attach it with::

        Memory(DefaultMemoryStore(request), namespace=lambda ctx: memory_namespace(request))

    **Built per request**, like ``DefaultStepStore`` and for the same reason: the
    protocol's methods carry no request, so it is bound at construction. Owner
    resolution runs inside each ``sync_to_async`` hop, because it may create a
    session row for the anonymous bucket and must stay off the event loop.

    **The path's namespace is not trusted as the boundary.** The harness composes
    the key as ``<namespace>/<agent_name>/<file>.md`` from a resolver the host
    supplies, and a ``/`` inside a resolved namespace is *accepted* — it simply
    opens further path segments. So a resolver reading anything user-controlled
    could otherwise address another scope. Filtering every query on the
    server-resolved ``owner_id`` makes that harmless, and
    [`memory_namespace`][django_pydantic_agent.memory_namespace] keeps the
    namespace valid in the first place.

    **Stored content cannot close the fence it is injected inside.** The harness
    wraps injected memory in ``<memory>`` markers and is explicit in its own
    README that this "is not a hard prompt-injection boundary"; without help, a
    note containing the closing tag ends the block early and everything after it
    reads as the user's own turn, durably, on every future run. Every write here
    escapes the angle brackets of both tags, so the stored bytes are safe for
    *every* consumer — the injection, ``read_memory``, ``search_memory`` and an
    app-side read alike. Doing it on write rather than read is also what keeps
    ``write_memory``'s ``old_text`` replacement working: the model edits against
    the same escaped text it was shown.

    **A per-owner ceiling, which the capability does not have.** Its
    ``max_memory_size`` bounds one file and its injection budget bounds what is
    *read*; nothing upstream caps how many files a namespace accumulates or how
    large they grow in total. ``max_files`` and ``max_total_chars`` do, at the
    write.

    **An anonymous request degrades rather than crashing.** With no owner and
    ``allow_anonymous`` off, every write no-ops and every read returns empty: the
    capability's hooks fire mid-run, so refusing by raising would abort the run,
    and an anonymous visitor has no durable identity worth remembering under.
    Note the asymmetry with the other stores — the memory tools are *model-facing*,
    so a no-op write still reports success to the model. Pair the store with an
    authenticated mount for memory to persist.

    Add ``"django_pydantic_agent.contrib.store"`` to ``INSTALLED_APPS`` and run
    ``migrate`` for the backing tables. Requires the
    ``django-pydantic-agent[harness]`` extra.
    """

    def __init__(
        self,
        request: HttpRequest | None = None,
        *,
        allow_anonymous: bool = False,
        max_files: int = _DEFAULT_MAX_FILES,
        max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
    ) -> None:
        """``request`` is optional, and which of the two modes you get depends on it.

        **Without one (the usual case), the store is namespace-scoped**: the owner
        is the leading segment of each path, which is the namespace the capability
        resolved. That is what lets it be constructed at *mount time*, where every
        transport takes its capability list and no request exists —
        ``AGUIServer(capabilities=[Memory(DefaultMemoryStore(), ...)])``. Pair it
        with
        [`memory_namespace_for_user`][django_pydantic_agent.memory_namespace_for_user],
        which derives that segment from the server-resolved ``ctx.deps.user``, and
        the namespace is a boundary the client cannot choose.

        **With one, the owner is resolved server-side from the request** and the
        path's namespace is not trusted at all, so a resolver reading anything
        user-controlled still cannot reach another owner's rows. Only available to
        a host that builds the store per request — a custom view, or a transport
        seam that hands one over.

        Pass ``allow_anonymous`` explicitly: this substrate reads no Django
        settings, and the value should match the conversation store's, so two
        endpoints sharing a persistence strategy agree on it. It applies only in
        the request-bound mode; without a request there is no anonymity to detect,
        and the namespace resolver decides what an anonymous caller is called."""
        self._request = request
        self._allow_anonymous: bool = allow_anonymous
        self._max_files: int = max_files
        self._max_total_chars: int = max_total_chars

    # -- MemoryStore protocol -------------------------------------------------

    async def read(self, path: str, *, max_chars: int) -> MemoryFile | None:
        _validate_path(path)
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        return await sync_to_async(self._read)(path, max_chars)

    async def get_operation(self, operation: MemoryOperation) -> MemoryMutation | None:
        return await sync_to_async(self._get_operation)(operation)

    async def write(
        self,
        path: str,
        content: str,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        _validate_path(path)
        return await sync_to_async(self._write)(path, content, expected_version, operation)

    async def delete(
        self,
        path: str,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        _validate_path(path)
        return await sync_to_async(self._delete)(path, expected_version, operation)

    async def list_paths(self, prefix: str = "", *, limit: int) -> list[str]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        return await sync_to_async(self._list_paths)(prefix, limit)

    # -- Erasure, which the protocol does not cover ---------------------------

    @staticmethod
    def purge(owner_id: str) -> int:
        """Delete every memory row for one owner, returning how many files went.

        Not part of ``MemoryStore``, and it has to be here because composing the
        protocol cannot express it: ``list_paths`` then ``delete`` needs the
        current version of each path, so a purge becomes an unbounded
        read-then-delete loop that a concurrent ``write_memory`` can lose to.
        Memory is durable personal data written *about* a user, so an erasure
        request has to be able to reach it in one statement.

        Deliberately **not** wired to a ``post_delete`` signal on the user model:
        whether deleting an account erases its memory is a product policy, and a
        library's job here is to make the operation possible.

        Synchronous — call it from a management command, an admin action, or an
        async caller through ``sync_to_async``.
        """
        StoredMemoryOperation.objects.filter(owner_id=owner_id).delete()
        deleted, _ = StoredMemory.objects.filter(owner_id=owner_id).delete()
        return deleted

    # -- Sync row operations (owner resolved off the event loop) --------------

    def _owner(self, path: str) -> str | None:
        """The owner every query for ``path`` filters on, or ``None`` to degrade.

        Namespace-scoped without a request: the leading path segment is the scope
        the capability resolved, and partitioning on it is what makes the store
        usable from a mount-time capability list. With a request, the owner is
        resolved from it instead and the path is not consulted -- strictly
        stronger, because then even a wrong namespace cannot cross scopes.
        """
        if self._request is None:
            return path.split("/", 1)[0]
        try:
            return resolve_owner_id(self._request, allow_anonymous=self._allow_anonymous)
        except AnonymousOperationError:
            return None

    def _read(self, path: str, max_chars: int) -> MemoryFile | None:
        owner = self._owner(path)
        if owner is None:
            return None
        row = StoredMemory.objects.filter(owner_id=owner, path=path).first()
        if row is None:
            return None
        return MemoryFile(
            content=row.content[:max_chars],
            version=row.version,
            operation_id=row.operation_id,
            truncated=len(row.content) > max_chars,
        )

    def _get_operation(self, operation: MemoryOperation) -> MemoryMutation | None:
        """A receipt lookup, which is the one call that carries no path.

        So there is no namespace to scope by, and in namespace mode the owner
        cannot be derived. Looking a receipt up by its id alone is nonetheless
        safe: the harness derives the id as a digest of ``(scope, run_id,
        tool_call_id)``, so the scope is already inside it and two owners cannot
        produce the same one. A request-bound store still filters by owner as
        well, because it can.
        """
        if self._request is None:
            return self._receipt(None, operation)
        owner = self._owner("")
        if owner is None:
            return None
        return self._receipt(owner, operation)

    @staticmethod
    def _receipt(owner: str | None, operation: MemoryOperation) -> MemoryMutation | None:
        """A prior result for ``operation``, or ``None`` if it has not run here.

        A known id whose fingerprint does not match is a reused id, not a replay:
        returning the old result would answer a different question than the one
        asked, so the protocol refuses instead.
        """
        rows = StoredMemoryOperation.objects.filter(operation_id=operation.id)
        if owner is not None:
            rows = rows.filter(owner_id=owner)
        row = rows.first()
        if row is None:
            return None
        if row.fingerprint != operation.fingerprint:
            raise MemoryOperationConflictError(
                f"operation id {operation.id!r} was reused with different arguments"
            )
        return MemoryMutation(version=row.version, replayed=True, existed=row.existed)

    def _write(
        self,
        path: str,
        content: str,
        expected_version: str | None,
        operation: MemoryOperation | None,
    ) -> MemoryMutation:
        owner = self._owner(path)
        if owner is None:
            return _unpersisted_mutation()
        with transaction.atomic():
            if operation is not None and (replay := self._receipt(owner, operation)) is not None:
                return replay
            # ``select_for_update`` rather than a bare read: two concurrent writes
            # that both read the same version would otherwise both pass the
            # compare-and-set and one would silently overwrite the other.
            row = StoredMemory.objects.select_for_update().filter(owner_id=owner, path=path).first()
            current_version = None if row is None else row.version
            if current_version != expected_version:
                raise MemoryConflictError(
                    f"memory path {path!r} changed before it could be written"
                )
            safe = _FENCE_TAG_RE.sub(_escape_tag, content)
            self._check_ceilings(owner, path, safe, replacing=row is not None)
            version = uuid4().hex
            operation_id = None if operation is None else operation.id
            if row is None:
                StoredMemory.objects.create(
                    owner_id=owner,
                    path=path,
                    content=safe,
                    version=version,
                    operation_id=operation_id,
                )
            else:
                StoredMemory.objects.filter(pk=row.pk).update(
                    content=safe, version=version, operation_id=operation_id
                )
            mutation = MemoryMutation(version=version, replayed=False, existed=row is not None)
            self._record(owner, operation, mutation)
            return mutation

    def _delete(
        self,
        path: str,
        expected_version: str | None,
        operation: MemoryOperation | None,
    ) -> MemoryMutation:
        owner = self._owner(path)
        if owner is None:
            return MemoryMutation(version=None, replayed=False, existed=False)
        with transaction.atomic():
            if operation is not None and (replay := self._receipt(owner, operation)) is not None:
                return replay
            row = StoredMemory.objects.select_for_update().filter(owner_id=owner, path=path).first()
            current_version = None if row is None else row.version
            if current_version != expected_version:
                raise MemoryConflictError(
                    f"memory path {path!r} changed before it could be deleted"
                )
            if row is not None:
                StoredMemory.objects.filter(pk=row.pk).delete()
            mutation = MemoryMutation(version=None, replayed=False, existed=row is not None)
            self._record(owner, operation, mutation)
            return mutation

    def _list_paths(self, prefix: str, limit: int) -> list[str]:
        # The prefix stands in for the path here, so in namespace mode an *empty*
        # prefix resolves to an empty owner and matches nothing. That is the right
        # way round: without a prefix there is no scope to answer for, and the
        # alternative would be listing every namespace in the table. The harness
        # always lists within a scope, so it never asks.
        owner = self._owner(prefix)
        if owner is None:
            return []
        rows = StoredMemory.objects.filter(owner_id=owner, path__startswith=prefix)
        return list(rows.order_by("path").values_list("path", flat=True)[:limit])

    def _check_ceilings(self, owner: str, path: str, content: str, *, replacing: bool) -> None:
        """Refuse a write that would push the owner past its file or byte ceiling.

        ``ModelRetry`` rather than a bare exception because the model is the one
        that can act on it — delete a file, or write less — and the harness's
        toolset re-raises it untouched, so it reaches the model as an instruction
        instead of an opaque tool failure. The row being replaced is excluded from
        both totals: an edit that shrinks a file must not be refused because the
        file it is shrinking already exists.
        """
        others = StoredMemory.objects.filter(owner_id=owner).exclude(path=path)
        if not replacing and others.count() + 1 > self._max_files:
            raise ModelRetry(
                f"This namespace already holds {self._max_files} memory files, the maximum. "
                "Delete one with `delete_memory` before creating another."
            )
        stored = sum(len(other) for other in others.values_list("content", flat=True))
        if stored + len(content) > self._max_total_chars:
            raise ModelRetry(
                f"Stored memory would exceed the {self._max_total_chars}-character total for "
                "this namespace. Remove or shorten an existing memory file first."
            )

    @staticmethod
    def _record(owner: str, operation: MemoryOperation | None, mutation: MemoryMutation) -> None:
        """Persist the idempotency receipt, when the call carried an operation."""
        if operation is None:
            return
        StoredMemoryOperation.objects.create(
            owner_id=owner,
            operation_id=operation.id,
            fingerprint=operation.fingerprint,
            version=mutation.version,
            existed=mutation.existed,
        )


def _validate_path(path: str) -> None:
    """Reject a path the ``StoredMemory.path`` column cannot hold.

    The harness caps each *segment* at 200 characters but never the joined path,
    so a deep namespace can compose one longer than the column. Raising here
    gives one consistent failure — the shape ``validate_store_path`` already
    raises — rather than a database error that differs per backend.
    """
    if len(path) > _MAX_PATH_CHARS:
        raise ValueError(f"memory path exceeds {_MAX_PATH_CHARS} characters: {path!r}")


def _escape_tag(match: re.Match[str]) -> str:
    """One fence tag with its angle brackets escaped, and nothing else touched.

    The family's own untrusted-context channel neutralises its marker by rewriting
    the *word*, which works because that marker is a hyphenated compound chosen so
    that mangling it still reads as prose. That trick does not port: this marker is
    ``memory``, an ordinary English word that belongs in ordinary notes, and
    rewriting every occurrence of it would corrupt the content the feature exists
    to store. Escaping only the bracket characters of a literal tag leaves the word
    intact, cannot be re-closed, and is idempotent — the escaped form contains no
    bracket left to match on a later write.
    """
    return match.group(0).replace("<", "&lt;").replace(">", "&gt;")


def _unpersisted_mutation() -> MemoryMutation:
    """The result of a write that degraded to a no-op.

    A version is fabricated rather than left ``None`` because the harness's
    toolset raises ``RuntimeError('memory write returned no version')`` on a
    ``None`` here, which would surface the anonymous degradation to the model as
    an internal error rather than a quiet no-op. Nothing is stored under it.
    """
    return MemoryMutation(version=uuid4().hex, replayed=False, existed=False)


__all__ = ["DefaultMemoryStore"]
