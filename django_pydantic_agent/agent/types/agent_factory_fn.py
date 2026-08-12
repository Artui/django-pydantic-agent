from __future__ import annotations

from typing import Any, Protocol

from pydantic_ai import Agent

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.registry.tool_registry import ToolRegistry


class AgentFactoryFn(Protocol):
    """The escape-hatch signature for a transport's ``agent_factory=`` argument.

    A callable of this shape fully replaces
    :func:`~django_pydantic_agent.agent.agent_factory.build_agent`, giving a
    project complete control over ``Agent`` construction. It receives the
    server-side tool registry and the calling transport's own resolved config
    record, which is untyped here because this substrate owns no settings
    namespace; each transport documents the concrete type its users receive.

    **The returned agent must be built with** ``deps_type=AgentDeps``. Transports
    hand every run an
    :class:`~django_pydantic_agent.agent.types.agent_deps.AgentDeps`, and that is
    how the acting user reaches spec tools and how AG-UI state reaches
    ``deps.state``. A factory that omits it produces an agent whose tools see no
    user.
    """

    def __call__(self, registry: ToolRegistry, config: Any) -> Agent[AgentDeps, Any]: ...


__all__ = ["AgentFactoryFn"]
