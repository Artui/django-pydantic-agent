"""Build a Pydantic-AI capability over drf-services specs, with no MCP hop.

Requires the ``django-pydantic-agent[spec-tools]`` extra, so consumers import
this module lazily and the dependency on ``djangorestframework-pydantic-ai``
stays optional.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_pydantic_agent.integrations.resolve_spec_mapping import resolve_spec_mapping
from django_pydantic_agent.integrations.types.spec_source import SpecSource


def build_spec_capability(
    specs: Mapping[str, Any] | SpecSource,
    *,
    exclude_names: frozenset[str] = frozenset(),
) -> Any:
    """A ``SpecCapability`` over ``specs``, acting as the run's ``deps.user``.

    Each spec is called in process through drf-services' transport-neutral
    surface, which enforces its ``permission_classes``. Nothing here closes over
    a request: ``SpecToolset``'s default extractor already reads
    ``ctx.deps.user``, so a run given
    [`AgentDeps`][django_pydantic_agent.AgentDeps] binds the
    acting user natively and the capability stays request-independent, which is
    what makes an agent built once reusable across runs.

    **Every spec here needs its own ``permission_classes``.** A spec with
    ``permission_classes=None`` makes ``SpecCapability`` raise
    ``ImproperlyConfigured`` rather than expose an ungated tool: over HTTP
    ``None`` means inherit — the view's classes, then
    ``DEFAULT_PERMISSION_CLASSES`` — and off HTTP there is nothing to inherit
    from, so a spec properly guarded behind a viewset would become callable by
    whatever the model decides to call.

    PAI's ``require_permissions=False`` migration flag is not exposed here; a
    project migrating a large registry constructs ``SpecCapability`` itself and
    passes it to ``AgentConfig(capabilities=...)``. That path skips the
    tool-catalog registration this function participates in, so tool-call cards
    render unlabelled — a migration step, not a destination.

    **A registry is passed through as a registry**, narrowed with its own
    ``subset`` when ``exclude_names`` bites. Flattening it to a mapping first
    would drop everything the entry carries beyond the spec -- including the
    ``AgentContract`` that says what a caller with no HTTP request has to be
    told -- and drop it *silently*, since the resulting toolset is well-formed
    and merely missing declarations nobody asked it for.

    Args:
        specs: A ``name -> ServiceSpec/SelectorSpec`` mapping, or a spec registry
            to read one from. Prefer the registry: it is the same specs plus the
            per-entry declarations an agent transport reads.
        exclude_names: Names a higher-precedence source (the ``@tool`` registry,
            the drf-mcp bridge) already claimed. They are dropped so that source
            wins the collision, since pydantic-ai raises ``UserError`` for a
            duplicate name at run time.
    """
    from rest_framework_pydantic_ai import SpecCapability

    resolved = resolve_spec_mapping(specs)
    selected = [name for name in resolved if name not in exclude_names]
    # ``subset`` is read off the object rather than declared on ``SpecSource``:
    # the protocol's one method is what tells a registry from a mapping, and a
    # source that only knows how to hand back specs is still a valid source.
    subset = getattr(specs, "subset", None)
    if subset is not None:
        return SpecCapability(subset(*selected))
    return SpecCapability({name: resolved[name] for name in selected})


__all__ = ["build_spec_capability"]
