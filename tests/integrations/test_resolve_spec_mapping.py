"""``resolve_spec_mapping`` — a spec mapping or a registry, structurally."""

from __future__ import annotations

from typing import Any

from rest_framework_services import ServiceSpec, SpecRegistry

from django_pydantic_agent.integrations.resolve_spec_mapping import resolve_spec_mapping
from django_pydantic_agent.integrations.types.spec_source import SpecSource


def _ok(user: Any) -> dict[str, Any]:
    """A no-op spec service."""
    return {"ok": True}


def _spec() -> ServiceSpec:
    return ServiceSpec(service=_ok, atomic=False)


def test_a_plain_mapping_passes_through_unchanged() -> None:
    specs = {"ping": _spec()}
    assert resolve_spec_mapping(specs) is specs


def test_a_registry_is_unwrapped_to_its_mapping() -> None:
    registry = SpecRegistry()
    spec = _spec()
    registry.register("ping", spec)

    assert resolve_spec_mapping(registry) == {"ping": spec}


def test_a_registry_and_its_dict_resolve_identically() -> None:
    registry = SpecRegistry()
    registry.register("ping", _spec())

    assert resolve_spec_mapping(registry) == resolve_spec_mapping(registry.specs())


def test_an_empty_registry_resolves_to_an_empty_mapping() -> None:
    assert resolve_spec_mapping(SpecRegistry()) == {}


def test_registration_order_survives() -> None:
    registry = SpecRegistry()
    registry.register("b", _spec())
    registry.register("a", _spec())

    assert list(resolve_spec_mapping(registry)) == ["b", "a"]


class TestSpecSourceProtocol:
    """The substrate names no drf-services type — the registry is matched by shape."""

    def test_a_registry_satisfies_it(self) -> None:
        assert isinstance(SpecRegistry(), SpecSource)

    def test_a_dict_does_not(self) -> None:
        # This is what makes "either a mapping or a registry" decidable at all.
        assert not isinstance({}, SpecSource)

    def test_any_object_with_specs_satisfies_it(self) -> None:
        """Structural, not nominal — nothing here imports SpecRegistry."""

        class _Stand_in:
            def specs(self) -> dict[str, Any]:
                return {"ping": _spec()}

        assert isinstance(_Stand_in(), SpecSource)
        assert list(resolve_spec_mapping(_Stand_in())) == ["ping"]


def test_iterating_a_registry_would_not_give_names() -> None:
    """The reason this helper is public rather than inlined in the builder.

    A transport that reserves tool names by iterating the raw argument gets
    ``RegisteredSpec`` records from a registry, not name strings — filling its
    collision-detection set with dataclasses and silently disabling the check.
    """
    registry = SpecRegistry()
    registry.register("ping", _spec())

    assert [type(entry).__name__ for entry in registry] == ["RegisteredSpec"]
    assert list(resolve_spec_mapping(registry)) == ["ping"]
