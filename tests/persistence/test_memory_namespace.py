from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from pydantic_ai_harness.memory._store import validate_store_path

from django_pydantic_agent.persistence.memory_namespace import memory_namespace
from django_pydantic_agent.persistence.utils import resolve_owner_id

# The harness composes the key as ``<namespace>/<agent_name>/<file>``, so a
# namespace is only usable if a path built from it validates.
_AGENT_SEGMENT = "main"


def _authed(pk: str) -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = SimpleNamespace(is_authenticated=True, pk=pk)  # type: ignore[attr-defined]
    return request


def _anon(session_key: str | None = "abc123") -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = SimpleNamespace(is_authenticated=False, pk=None)  # type: ignore[attr-defined]
    created: list[bool] = []

    def create() -> None:
        created.append(True)
        request.session.session_key = "created-key"  # type: ignore[attr-defined]

    request.session = SimpleNamespace(session_key=session_key, create=create)  # type: ignore[attr-defined]
    return request


def _path(namespace: str) -> str:
    return f"{namespace}/{_AGENT_SEGMENT}/MEMORY.md"


def test_the_owner_id_the_other_stores_use_is_not_a_valid_namespace() -> None:
    """The defect this resolver exists for, pinned against the naive wiring.

    Reaching for ``resolve_owner_id`` — the id the conversation, attachment and
    step stores all partition on — is the obvious way to scope memory per user,
    and it aborts every anonymous run: the colon is outside the segment alphabet
    the harness accepts, and the raise happens during namespace resolution, which
    sits outside the store read that ``injection_errors`` guards.
    """
    owner_id = resolve_owner_id(_anon(), allow_anonymous=True)

    assert owner_id == "anon:abc123"
    with pytest.raises(ValueError, match="invalid memory path"):
        validate_store_path(_path(owner_id))


@pytest.mark.parametrize(
    "pk",
    [
        "7",
        "01917f4e-2f6d-7c3a-9d54-1f2b3c4d5e6f",
        "user.name_1-2",
        # Shapes a custom user model really produces, none of them segment-safe.
        "person@example.com",
        "tenant/42",
        "a..b",
        "x" * 200,
    ],
)
def test_every_authenticated_namespace_is_a_valid_path_segment(pk: str) -> None:
    validate_store_path(_path(memory_namespace(_authed(pk))))


def test_an_anonymous_namespace_is_a_valid_path_segment() -> None:
    validate_store_path(_path(memory_namespace(_anon())))


def test_a_session_is_created_when_the_browser_has_none() -> None:
    request = _anon(session_key=None)

    namespace = memory_namespace(request)

    assert namespace == "anon-created-key"


def test_a_segment_safe_primary_key_is_carried_through_readably() -> None:
    """Kept legible on purpose: an operator reading a stored path should be able
    to tell whose it is without a lookup table."""
    assert memory_namespace(_authed("7")) == "u-7"


def test_two_primary_keys_differing_only_in_unsafe_characters_stay_apart() -> None:
    """The reason unsafe ids are digested rather than sanitised.

    Stripping or replacing the offending characters maps ``tenant/42`` and
    ``tenant-42`` onto one namespace, which is two users reading each other's
    memory — the exact failure the resolver exists to prevent.
    """
    assert memory_namespace(_authed("tenant/42")) != memory_namespace(_authed("tenant-42"))


def test_an_over_long_primary_key_is_digested_because_the_prefix_no_longer_fits() -> None:
    """200 characters is a valid segment; ``u-`` plus 200 is not."""
    namespace = memory_namespace(_authed("x" * 200))

    assert namespace != f"u-{'x' * 200}"
    assert len(namespace) == len("u-") + 40


def test_an_anonymous_and_an_authenticated_namespace_cannot_collide() -> None:
    assert memory_namespace(_anon()) != memory_namespace(_authed("abc123"))


def test_the_resolver_is_stable_for_the_same_identifier() -> None:
    assert memory_namespace(_authed("person@example.com")) == memory_namespace(
        _authed("person@example.com")
    )
