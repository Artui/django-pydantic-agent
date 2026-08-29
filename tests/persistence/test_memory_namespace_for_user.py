from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai_harness.memory._store import validate_store_path

from django_pydantic_agent.persistence.memory_namespace_for_user import memory_namespace_for_user


def _user(pk: str) -> SimpleNamespace:
    return SimpleNamespace(is_authenticated=True, pk=pk)


def _path(namespace: str) -> str:
    return f"{namespace}/main/MEMORY.md"


@pytest.mark.parametrize(
    "pk",
    [
        "7",
        "01917f4e-2f6d-7c3a-9d54-1f2b3c4d5e6f",
        "person@example.com",
        "tenant/42",
        "a..b",
        "x" * 200,
    ],
)
def test_every_namespace_is_a_valid_path_segment(pk: str) -> None:
    validate_store_path(_path(memory_namespace_for_user(_user(pk))))


def test_a_segment_safe_primary_key_is_carried_through_readably() -> None:
    assert memory_namespace_for_user(_user("7")) == "u-7"


def test_two_primary_keys_differing_only_in_unsafe_characters_stay_apart() -> None:
    assert memory_namespace_for_user(_user("tenant/42")) != memory_namespace_for_user(
        _user("tenant-42")
    )


@pytest.mark.parametrize(
    "user",
    [None, SimpleNamespace(is_authenticated=False, pk=None)],
)
def test_an_unauthenticated_caller_gets_the_shared_anonymous_namespace(user: object) -> None:
    """Documented limitation, not an oversight: with no request there is no
    session to key on, so this cannot be a per-visitor bucket."""
    assert memory_namespace_for_user(user) == "anon"
    validate_store_path(_path(memory_namespace_for_user(user)))


def test_an_anonymous_namespace_cannot_collide_with_an_authenticated_one() -> None:
    assert memory_namespace_for_user(None) != memory_namespace_for_user(_user("anon"))
