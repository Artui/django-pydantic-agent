from __future__ import annotations

from typing import Any, Protocol

from pydantic_ai import Agent

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.registry.tool_registry import ToolRegistry


class AgentFactoryFn(Protocol):
    """The escape-hatch signature for a transport's ``agent_factory=`` argument.

    A callable matching this shape fully replaces the built-in
    :func:`~django_pydantic_agent.agent.agent_factory.build_agent`, giving a project
    complete control over Pydantic-AI ``Agent`` construction (custom model
    providers, output types, toolsets, instrumentation, …). It receives the
    server-side tool registry and **that transport's resolved config object**.

    ``config`` is deliberately untyped here: this substrate reads no settings and
    owns no settings namespace, so the second argument is whatever configuration
    record the calling transport resolved — ``AGUIConfig`` for the AG-UI
    transport, its own equivalent for another. Each transport documents the
    concrete type its users receive.

    **The returned agent must be typed** ``Agent[AgentDeps, ...]`` — build it
    with ``deps_type=AgentDeps``. Transports hand every run an
    :class:`~django_pydantic_agent.agent.types.agent_deps.AgentDeps`, and that
    is how the acting user reaches spec tools (``ctx.deps.user``) and how AG-UI
    state reaches ``deps.state``. A factory that omits ``deps_type`` produces an
    agent whose tools see no user.
    """

    def __call__(self, registry: ToolRegistry, config: Any) -> Agent[AgentDeps, Any]: ...


__all__ = ["AgentFactoryFn"]
