"""``resolve_spec_mapping`` — normalise a spec mapping or registry to a mapping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_pydantic_agent.integrations.types.spec_source import SpecSource


def resolve_spec_mapping(specs: Mapping[str, Any] | SpecSource) -> Mapping[str, Any]:
    """Return the ``name -> spec`` mapping, whether given one or a registry.

    A [`SpecSource`][django_pydantic_agent.integrations.types.spec_source.SpecSource]
    is recognised structurally, by having a ``specs()`` method.

    Public because a transport needs the same normalisation *before*
    [`build_spec_capability`][django_pydantic_agent.integrations.build_spec_capability.build_spec_capability]
    runs, when it reserves each tool name for collision detection: iterating a
    registry yields ``RegisteredSpec`` records rather than names, so a transport
    that iterated the raw argument would fill its set with dataclasses and
    silently stop detecting collisions.
    """
    return specs.specs() if isinstance(specs, SpecSource) else specs


__all__ = ["resolve_spec_mapping"]
