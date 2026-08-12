from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import AllowAny
from rest_framework_services import ServiceSpec

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.integrations.build_spec_capability import build_spec_capability


def _ok(user: Any) -> dict[str, Any]:
    """A no-op spec service."""
    return {"ok": True}


def test_an_unguarded_spec_is_refused_rather_than_exposed() -> None:
    """The strict default reaches a consumer straight through this builder.

    ``permission_classes=None`` means inherit over HTTP, but a capability
    dispatches off HTTP with nothing to inherit from, so a spec correctly guarded
    behind a viewset would become callable by whatever the model decided to call.
    Asserted here rather than only upstream because this function is how every
    consumer builds the capability.
    """
    with pytest.raises(ImproperlyConfigured, match="no permission_classes"):
        build_spec_capability({"ping": ServiceSpec(service=_ok, atomic=False)})


def test_excludes_registry_names() -> None:
    spec = ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny])
    capability = build_spec_capability(
        {"ping": spec, "dup": spec},
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
        {"ping": ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny])}
    )
    assert capability.get_instructions() is None

    instructions = await capability.get_toolset().get_instructions(None)
    assert instructions is not None
    assert "error" in instructions.lower()


async def test_binds_the_acting_user_from_the_run_deps() -> None:
    """The whole point of the change: the user arrives on ``ctx.deps``, so
    nothing closes over a request and the capability is request-independent."""
    seen: dict[str, Any] = {}

    def ping(user: Any) -> dict[str, Any]:
        """Ping."""
        seen["user"] = user
        return {"ok": True}

    user = SimpleNamespace(name="alice")
    capability = build_spec_capability(
        {"ping": ServiceSpec(service=ping, atomic=False, permission_classes=[AllowAny])}
    )

    toolset = capability.get_toolset()
    ctx = SimpleNamespace(deps=AgentDeps(user=user))
    assert await toolset.call_tool("ping", {}, ctx, None) == {"ok": True}
    assert seen["user"] is user


async def test_one_capability_serves_two_users() -> None:
    """What the request closure made impossible — the same capability, two runs,
    two acting users. This is what unblocks reusing a built agent."""
    seen: list[Any] = []

    def whoami(user: Any) -> dict[str, Any]:
        """Whoami."""
        seen.append(user)
        return {"ok": True}

    capability = build_spec_capability(
        {"whoami": ServiceSpec(service=whoami, atomic=False, permission_classes=[AllowAny])}
    )
    toolset = capability.get_toolset()

    alice, bob = SimpleNamespace(name="alice"), SimpleNamespace(name="bob")
    await toolset.call_tool("whoami", {}, SimpleNamespace(deps=AgentDeps(user=alice)), None)
    await toolset.call_tool("whoami", {}, SimpleNamespace(deps=AgentDeps(user=bob)), None)

    assert seen == [alice, bob]


def test_accepts_a_spec_registry() -> None:
    from rest_framework_services import SpecRegistry

    registry = SpecRegistry()
    registry.register("ping", ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny]))

    capability = build_spec_capability(registry)
    assert set(capability.get_toolset()._specs) == {"ping"}


def test_exclude_names_apply_to_a_registry_too() -> None:
    from rest_framework_services import SpecRegistry

    registry = SpecRegistry()
    registry.register("ping", ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny]))
    registry.register("dup", ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny]))

    capability = build_spec_capability(registry, exclude_names=frozenset({"dup"}))
    assert set(capability.get_toolset()._specs) == {"ping"}
