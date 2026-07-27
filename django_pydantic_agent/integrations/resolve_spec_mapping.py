"""``resolve_spec_mapping`` — normalise a spec mapping or registry to a mapping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_pydantic_agent.integrations.types.spec_source import SpecSource


def resolve_spec_mapping(specs: Mapping[str, Any] | SpecSource) -> Mapping[str, Any]:
    """Return the ``name -> spec`` mapping, whether given one or a registry.

    A :class:`~django_pydantic_agent.integrations.types.spec_source.SpecSource`
    (drf-services' ``SpecRegistry``) is recognised **structurally**, by having a
    ``specs()`` method — this substrate names no drf-services type; see
    ``SpecSource`` for why.

    Public rather than private because a transport needs the same normalisation
    *before* :func:`~django_pydantic_agent.integrations.build_spec_capability.build_spec_capability`
    runs: the AG-UI view reserves each tool name into its collision-detection
    set, and iterating a registry yields ``RegisteredSpec`` records rather than
    names — so a transport that iterated the raw argument would fill that set
    with dataclasses and silently stop detecting collisions between the ``@tool``
    registry, the drf-mcp bridge and these spec tools.
    """
    return specs.specs() if isinstance(specs, SpecSource) else specs


__all__ = ["resolve_spec_mapping"]
