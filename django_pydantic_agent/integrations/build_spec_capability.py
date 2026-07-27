"""Build a Pydantic-AI capability over drf-services specs — no MCP hop.

Requires the ``django-pydantic-agent[spec-tools]`` extra. Imported lazily, only
when spec tools are configured, so the dependency on
``djangorestframework-pydantic-ai`` (and drf-services) stays optional.

Unlike :mod:`~django_pydantic_agent.integrations.drf_mcp`, there is no MCP server in the
path: ``djangorestframework-pydantic-ai``'s ``SpecCapability`` wraps a
``SpecToolset`` that calls the specs in process through drf-services'
transport-neutral surface (`dispatch_spec` + its off-HTTP helpers), enforcing
each spec's ``permission_classes``. The agent acts as the **logged-in AG-UI
user**, bound the way pydantic-ai intends: ``SpecToolset``'s own default reads
``ctx.deps.user`` off :class:`~django_pydantic_agent.agent.types.agent_deps.AgentDeps`,
so nothing here closes over the request.

Choosing the *capability* over the bare ``SpecToolset`` is deliberate — but the
reason is no longer instructions. Since PAI 0.6.0 the conventions (``page`` /
``limit`` / ``order`` on list tools, and the error contract — an ``{"error": …}``
result is a final answer, a retry message means fix the argument, a permission
error is final) live on ``SpecToolset.get_instructions()``, so they reach the
system prompt whether the toolset is attached directly or wrapped here; the
capability *delegates* and returns ``None`` of its own, precisely so pydantic-ai
does not collect the same block twice. What wrapping still buys is the
capability seam itself: ``defer_loading``, and a uniform place for the
transport to compose spec tools alongside audit / guard capabilities.
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

    ``specs`` is a ``name -> ServiceSpec/SelectorSpec`` mapping supplied by the
    caller, or a spec registry to read that mapping from. Names in
    ``exclude_names`` — the names a
    higher-precedence source (the ``@tool`` registry, the drf-mcp bridge) already
    claimed — are dropped so that source wins a collision (the same rule
    ``build_tool_catalog`` applies); otherwise pydantic-ai raises ``UserError``
    for the duplicate name at run time.

    **No ``get_user`` override, and no ``request``.** ``SpecToolset``'s default
    extractor already reads ``ctx.deps.user``, so a run given
    :class:`~django_pydantic_agent.agent.types.agent_deps.AgentDeps` binds the
    acting user natively. The capability is therefore request-independent, which
    is what makes an agent built once reusable across runs.
    """
    from rest_framework_pydantic_ai import SpecCapability

    resolved = resolve_spec_mapping(specs)
    selected = {name: spec for name, spec in resolved.items() if name not in exclude_names}
    return SpecCapability(selected)


__all__ = ["build_spec_capability"]
