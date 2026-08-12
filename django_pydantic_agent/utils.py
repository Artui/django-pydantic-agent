"""Cross-package helpers shared by the agent endpoint and the catalog views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async
from django.http import HttpRequest, JsonResponse

# The ``get_user`` hook: sync or async, returning the acting user. Sync hooks may
# use the ORM freely — the async callers below run them off the event loop.
GetUser = Callable[[HttpRequest], Any] | Callable[[HttpRequest], Awaitable[Any]]

# The ``authorize`` predicate: run *after* the acting user is established,
# returning ``True`` to allow. Rejecting denies with 403 (authenticated but
# forbidden), as distinct from ``require_authenticated``'s 401 (no user at all).
# The async caller runs it off the event loop, so reading the ORM stays safe.
AuthorizePredicate = Callable[[HttpRequest], bool]


async def acall_get_user(hook: GetUser, request: HttpRequest) -> Any:
    """Run a ``get_user`` hook from async code, ORM-safe either way.

    Detection has to happen before the call: a sync ORM hook must not be invoked
    on the loop thread even once. Sync hooks therefore go through
    ``sync_to_async(thread_sensitive=True)``, Django's shared sync executor,
    where the ORM, transactions and thread-locals behave. A sync callable that
    returns a coroutine anyway is awaited rather than leaked onto
    ``request.user``.
    """
    if iscoroutinefunction(hook):
        return await hook(request)
    result = await sync_to_async(hook, thread_sensitive=True)(request)
    return await result if isawaitable(result) else result


def call_get_user(hook: GetUser, request: HttpRequest) -> Any:
    """Sync twin of :func:`acall_get_user` for the sync catalog views.

    A sync view already runs in a worker thread under ASGI, so a sync ORM hook
    needs no hop here; only an async hook is bridged.
    """
    if iscoroutinefunction(hook):
        return async_to_sync(hook)(request)
    result = hook(request)
    if isawaitable(result):
        return async_to_sync(_consume)(result)
    return result


async def _consume(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def materialize_request_user(request: HttpRequest) -> Any:
    """Force the lazy ``request.user`` and return it — call **off** the loop.

    With DB-backed sessions the first touch of the ``SimpleLazyObject`` runs the
    session and user queries, which Django forbids on the event loop. Forcing it
    in a worker thread caches the resolved user on the wrapper, so every later
    read is loop-safe. Django 5 has ``request.auser()``; the floor is 4.2, so
    the thread hop is the portable spelling.
    """
    user = getattr(request, "user", None)
    getattr(user, "is_authenticated", False)
    return user


async def aauthorize(
    request: HttpRequest,
    *,
    get_user: GetUser | None,
    require_authenticated: bool,
    authorize: AuthorizePredicate | None = None,
) -> int | None:
    """The shared authorize policy, async flavour.

    Establishes the acting user — through ``get_user`` when supplied, else by
    materializing the middleware's lazy user off the loop — then applies both
    gates in order. Returns the status to deny with, or ``None`` to allow: 401
    when ``require_authenticated`` is set and the user is anonymous, 403 when an
    ``authorize`` predicate rejects an established user. The predicate runs off
    the event loop, so it may read the ORM.
    """
    if get_user is not None:
        request.user = await acall_get_user(get_user, request)
        user: Any = request.user
    else:
        user = await sync_to_async(materialize_request_user, thread_sensitive=True)(request)
    if require_authenticated and not getattr(user, "is_authenticated", False):
        return 401
    if authorize is not None and not await sync_to_async(authorize, thread_sensitive=True)(request):
        return 403
    return None


def authorize(
    request: HttpRequest,
    *,
    get_user: GetUser | None,
    require_authenticated: bool,
    authorize: AuthorizePredicate | None = None,
) -> int | None:
    """Sync flavour of :func:`aauthorize`, for the catalog views.

    Same policy and same deny-status return; no thread hops are needed.
    """
    if get_user is not None:
        request.user = call_get_user(get_user, request)
        user: Any = request.user
    else:
        user = getattr(request, "user", None)
    if require_authenticated and not getattr(user, "is_authenticated", False):
        return 401
    if authorize is not None and not authorize(request):
        return 403
    return None


def auth_error_response(status: int) -> JsonResponse:
    """The JSON deny response for an :func:`authorize` / :func:`aauthorize` status.

    Always JSON, never an HTML login redirect.
    """
    message = "authentication required" if status == 401 else "forbidden"
    return JsonResponse({"error": message}, status=status)
