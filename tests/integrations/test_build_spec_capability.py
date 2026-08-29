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


def _capture_source(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record what this builder actually hands to ``SpecCapability``."""
    import rest_framework_pydantic_ai

    captured: list[Any] = []

    def _stub(source: Any, **kwargs: Any) -> Any:
        captured.append(source)
        return SimpleNamespace()

    monkeypatch.setattr(rest_framework_pydantic_ai, "SpecCapability", _stub)
    return captured


class TestARegistryIsNotFlattened:
    """The entry carries more than the spec, and only the entry carries it.

    A registry entry holds the per-entry declarations an agent transport reads
    -- its tags, and the ``OfflineContract`` saying what a caller with no HTTP
    request has to be told. ``specs()`` returns ``name -> spec`` and drops all
    of it, which is why this builder must pass the source through rather than
    normalise it first. The failure is silent: the toolset is well-formed and
    merely missing declarations nobody asked it for.
    """

    def test_the_registry_reaches_the_capability_as_a_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rest_framework_services import SpecRegistry

        captured = _capture_source(monkeypatch)
        registry = SpecRegistry()
        registry.register(
            "ping",
            ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny]),
            tags=("read",),
        )

        build_spec_capability(registry)

        # ``specs()`` is what tells a registry from a mapping -- the same test
        # ``resolve_spec_mapping`` uses -- and the entry is what survives.
        (source,) = captured
        assert callable(getattr(source, "specs", None))
        assert source.get("ping").tags == frozenset({"read"})

    def test_narrowing_by_exclude_names_keeps_it_a_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rest_framework_services import SpecRegistry

        captured = _capture_source(monkeypatch)
        registry = SpecRegistry()
        registry.register(
            "ping",
            ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny]),
            tags=("read",),
        )
        registry.register(
            "dup", ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny])
        )

        build_spec_capability(registry, exclude_names=frozenset({"dup"}))

        (source,) = captured
        assert set(source.specs()) == {"ping"}
        assert source.get("ping").tags == frozenset({"read"})

    def test_a_plain_mapping_is_still_a_plain_mapping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_source(monkeypatch)
        spec = ServiceSpec(service=_ok, atomic=False, permission_classes=[AllowAny])

        build_spec_capability({"ping": spec, "dup": spec}, exclude_names=frozenset({"dup"}))

        assert captured == [{"ping": spec}]


async def test_a_specs_progress_reports_reach_the_runs_sink() -> None:
    """The other half of what rides ``ctx.deps``.

    ``SpecToolset`` reads its reporter off ``ctx.deps.progress`` exactly as it
    reads the user off ``ctx.deps.user``. With no such field the reports resolved
    to drf-services' no-op and a long-running spec reported into nothing, with no
    warning anywhere.
    """
    reports: list[tuple[int, int | None, str | None]] = []

    def sink(
        progress: int, *, total: int | None = None, message: str | None = None, **_: Any
    ) -> None:
        reports.append((progress, total, message))

    def import_rows(user: Any, progress: Any) -> dict[str, Any]:
        """Import rows."""
        progress(45, total=100, message="importing rows")
        return {"ok": True}

    capability = build_spec_capability(
        {
            "import_rows": ServiceSpec(
                service=import_rows, atomic=False, permission_classes=[AllowAny]
            )
        }
    )
    ctx = SimpleNamespace(deps=AgentDeps(user=SimpleNamespace(name="alice"), progress=sink))

    await capability.get_toolset().call_tool("import_rows", {}, ctx, None)

    assert reports == [(45, 100, "importing rows")]
