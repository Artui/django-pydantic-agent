from __future__ import annotations

from typing import Any, cast

from pydantic_ai import Agent, DeferredToolRequests

from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.audit.audit_capability import AuditCapability
from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger
from django_pydantic_agent.policy.failure.tool_failure_policy import ToolFailurePolicy
from django_pydantic_agent.policy.guard.tool_guard import ToolGuard
from django_pydantic_agent.registry.tool_registry import ToolRegistry


def build_agent(registry: ToolRegistry, config: AgentConfig) -> Agent[AgentDeps, Any]:
    """Build a Pydantic-AI ``Agent`` from a registry and an
    [`AgentConfig`][django_pydantic_agent.AgentConfig].

    Each registry tool is registered as a plain Pydantic-AI tool, and ``config``
    supplies the model, the toolsets and capabilities composed alongside them,
    and the policies below. Frontend tools declared in the AG-UI
    ``RunAgentInput`` are merged by the adapter, not registered here.

    Three capabilities are added from ``config`` on request: an
    [`AuditCapability`][django_pydantic_agent.AuditCapability] that times and
    records **every** tool the agent runs — registry tools and composed toolsets
    alike — a [`ToolGuard`][django_pydantic_agent.ToolGuard] that flips
    destructive tools to require approval, and a
    [`ToolFailurePolicy`][django_pydantic_agent.ToolFailurePolicy], on unless
    turned off, so a raising tool fails its own call rather than the whole run.
    Each declares its position through
    ``get_ordering`` and pydantic-ai sorts them, so the list needs no
    pre-ordering.

    The agent is typed ``Agent[AgentDeps, ...]``, so every run must be given
    [`AgentDeps`][django_pydantic_agent.AgentDeps] through ``deps=``.
    """
    capabilities = list(config.capabilities) if config.capabilities is not None else []
    if config.audit_logger is not None and not isinstance(config.audit_logger, NullAuditLogger):
        capabilities.append(
            AuditCapability(config.audit_logger, ip_address=config.audit_ip_address),
        )
    if config.tool_guard is not None and config.tool_guard.enabled:
        capabilities.append(ToolGuard(registry, config=config.tool_guard))
    if config.tool_failure.enabled:
        # No ordering constraint against the audit capability: the two ride
        # different hooks (``on_tool_execute_error`` here, ``wrap_tool_execute``
        # there), so the failure is recorded and then converted either way.
        capabilities.append(ToolFailurePolicy(config.tool_failure))
    return Agent(
        model=config.model,
        deps_type=AgentDeps,
        tools=[binding.spec.fn for binding in registry],
        # ``DeferredToolRequests`` turns on the approval interrupt loop for
        # *server-side* tools. The AG-UI adapter augments ``output_type`` only
        # when the run carries frontend tools, so a run whose only gated tool is
        # server-side would never defer; setting it here makes approval
        # independent of them, and pydantic-ai dedups when the adapter augments
        # too. Cast for the same reason as ``model_settings``: ty cannot bind
        # ``OutputDataT`` from a heterogeneous list literal, so the call matches
        # no overload and the return degrades to unsolved typevars.
        output_type=cast("Any", [str, DeferredToolRequests]),
        instructions=config.instructions,
        # ``model_settings`` is a plain dict at the settings boundary; Agent
        # types it as the ``ModelSettings`` TypedDict.
        model_settings=cast("Any", config.model_settings),
        retries=config.retries,
        toolsets=list(config.toolsets) if config.toolsets is not None else None,
        capabilities=capabilities or None,
    )


__all__ = ["build_agent"]
