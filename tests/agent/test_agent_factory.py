from __future__ import annotations

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry


class _CapturingLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_build_agent_returns_agent_with_registry_tools() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    agent = build_agent(reg, AgentConfig(model=TestModel()))
    assert isinstance(agent, Agent)


def test_build_agent_puts_deferred_tool_requests_in_output_type() -> None:
    # Turns on the tool-approval interrupt loop for *server-side* tools. The
    # AG-UI adapter only augments ``output_type`` with ``DeferredToolRequests``
    # when the run carries frontend tools, so a server-only gated tool would
    # otherwise crash the run with a RUN_ERROR ("a deferred tool call was
    # present, but DeferredToolRequests is not among output types"). Setting it
    # on the Agent makes the approval path independent of frontend tools.
    agent = build_agent(ToolRegistry(), AgentConfig(model=TestModel()))
    assert DeferredToolRequests in agent.output_type


def test_build_agent_accepts_model_settings_retries_toolsets_capabilities() -> None:
    from pydantic_ai.toolsets import FunctionToolset

    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    agent = build_agent(
        reg,
        AgentConfig(
            model=TestModel(),
            model_settings={"temperature": 0.1},
            retries=2,
            toolsets=[FunctionToolset()],
            capabilities=[object()],
        ),
    )
    assert isinstance(agent, Agent)


async def test_audited_sync_tool_records_success() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    await agent.run("double 3")

    assert audit.events, "expected at least one audited call"
    event = audit.events[0]
    assert event.tool_name == "double"
    assert event.success is True
    assert event.result_size is not None


async def test_audited_sync_tool_records_failure() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def boom(n: int) -> int:
        """Always explodes."""
        raise ValueError("kaboom")

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    # The run survives the raising tool (the default tool-failure policy turns
    # it into a failed result); what this test guards is that the operator's
    # record still names the tool and carries the real exception text.
    await agent.run("call boom with 1")

    failures = [e for e in audit.events if not e.success]
    assert failures
    assert "kaboom" in (failures[0].error or "")


async def test_audited_async_tool_records_success() -> None:
    reg = ToolRegistry()

    @tool(reg)
    async def afetch(label: str) -> str:
        """Fetch something asynchronously."""
        return f"value:{label}"

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    await agent.run("afetch x")

    successes = [e for e in audit.events if e.success and e.tool_name == "afetch"]
    assert successes


async def test_audited_async_tool_records_failure() -> None:
    reg = ToolRegistry()

    @tool(reg)
    async def aboom(label: str) -> str:
        """Async explosion."""
        raise RuntimeError("async kaboom")

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    await agent.run("aboom x")

    failures = [e for e in audit.events if not e.success and e.tool_name == "aboom"]
    assert failures
    assert "async kaboom" in (failures[0].error or "")


async def test_no_audit_logger_is_a_noop_default() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    # No audit_logger → NullAuditLogger; run must still succeed.
    agent = build_agent(reg, AgentConfig(model=TestModel()))
    result = await agent.run("double 4")
    assert result.output is not None


def test_tool_guard_is_composed_when_enabled() -> None:
    from django_pydantic_agent.policy.guard.tool_guard import ToolGuard
    from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig

    reg = ToolRegistry()

    @tool(reg, destructive=True)
    def drop_it(name: str) -> str:
        """Delete a thing."""
        return name

    agent = build_agent(
        reg,
        AgentConfig(model=TestModel(), tool_guard=ToolGuardConfig(enabled=True)),
    )
    root = agent.root_capability
    composed = getattr(root, "capabilities", [root])
    assert any(isinstance(c, ToolGuard) for c in composed)


def test_tool_guard_is_absent_when_disabled() -> None:
    from django_pydantic_agent.policy.guard.tool_guard import ToolGuard
    from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig

    agent = build_agent(
        ToolRegistry(),
        AgentConfig(model=TestModel(), tool_guard=ToolGuardConfig(enabled=False)),
    )
    root = agent.root_capability
    composed = getattr(root, "capabilities", [root])
    assert not any(isinstance(c, ToolGuard) for c in composed)
