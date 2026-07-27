from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory
from rest_framework_services import ServiceSpec

from django_pydantic_agent.integrations.build_spec_capability import build_spec_capability


def _request(user: Any) -> HttpRequest:
    request = RequestFactory().post("/agent/")
    request.user = user  # type: ignore[attr-defined]
    return request


def _ok(user: Any) -> dict[str, Any]:
    """A no-op spec service."""
    return {"ok": True}


def test_excludes_registry_names() -> None:
    spec = ServiceSpec(service=_ok, atomic=False)
    capability = build_spec_capability(
        {"ping": spec, "dup": spec},
        _request(SimpleNamespace()),
        exclude_names=frozenset({"dup"}),
    )
    # The registry-owned name is dropped (registry wins the collision); the
    # capability's underlying toolset exposes only the surviving name.
    assert set(capability.get_toolset()._specs) == {"ping"}


async def test_carries_the_spec_conventions_to_the_model() -> None:
    # The conventions (the error contract, the list-tool pagination args) reach
    # the system prompt from the *toolset*, not the capability. PAI 0.6.0 moved
    # them onto ``SpecToolset.get_instructions()`` so they land whether a toolset
    # is attached directly or wrapped here, and made the capability delegate —
    # it returns None so pydantic-ai doesn't collect the same block twice.
    capability = build_spec_capability(
        {"ping": ServiceSpec(service=_ok, atomic=False)}, _request(SimpleNamespace())
    )
    assert capability.get_instructions() is None

    instructions = await capability.get_toolset().get_instructions(None)
    assert instructions is not None
    assert "error" in instructions.lower()


async def test_binds_the_request_user_ignoring_run_context() -> None:
    seen: dict[str, Any] = {}

    def ping(user: Any) -> dict[str, Any]:
        """Ping."""
        seen["user"] = user
        return {"ok": True}

    user = SimpleNamespace(name="alice")
    capability = build_spec_capability(
        {"ping": ServiceSpec(service=ping, atomic=False)}, _request(user)
    )
    # The run context is ignored — get_user returns the bound request.user.
    toolset = capability.get_toolset()
    result = await toolset.call_tool("ping", {}, SimpleNamespace(deps=None), None)
    assert result == {"ok": True}
    assert seen["user"] is user


def test_accepts_a_spec_registry() -> None:
    from rest_framework_services import SpecRegistry

    registry = SpecRegistry()
    registry.register("ping", ServiceSpec(service=_ok, atomic=False))

    capability = build_spec_capability(registry, _request(SimpleNamespace()))
    assert set(capability.get_toolset()._specs) == {"ping"}


def test_exclude_names_apply_to_a_registry_too() -> None:
    from rest_framework_services import SpecRegistry

    registry = SpecRegistry()
    registry.register("ping", ServiceSpec(service=_ok, atomic=False))
    registry.register("dup", ServiceSpec(service=_ok, atomic=False))

    capability = build_spec_capability(
        registry, _request(SimpleNamespace()), exclude_names=frozenset({"dup"})
    )
    assert set(capability.get_toolset()._specs) == {"ping"}
