from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.audit.audit_capability import AuditCapability
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent


def test_audit_declares_outermost_ordering() -> None:
    # Audit is the observability layer — it must wrap every other capability's
    # execution hooks. Declaring the position (rather than relying on list order
    # at the build_agent call site) is what keeps composition deterministic once
    # a second capability (ToolGuard) joins the chain.
    ordering = AuditCapability(_CapturingLogger()).get_ordering()
    assert ordering is not None
    assert ordering.position == "outermost"


class _CapturingLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class _RaisingLogger:
    def record(self, event: AuditEvent) -> None:
        raise RuntimeError("sink down")


async def test_records_toolset_tools_not_just_registry_tools() -> None:
    # The capability hooks the run loop, so tools contributed by a composed
    # toolset (drf-mcp / spec / attachment / skills) are audited too — the old
    # per-tool wrapper saw only registry tools.
    def triple(n: int) -> int:
        """Triple a number."""
        return n * 3

    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["triple"]),
        toolsets=[FunctionToolset([triple])],
        capabilities=[AuditCapability(audit)],
    )
    await agent.run("triple 2")
    assert [e.tool_name for e in audit.events] == ["triple"]
    event = audit.events[0]
    assert event.success is True
    assert event.result_size is not None
    assert '"n"' in event.arguments_repr


async def test_stamps_ip_and_organization_onto_events() -> None:
    def ping() -> str:
        """Ping."""
        return "pong"

    audit = _CapturingLogger()
    capability = AuditCapability(audit, ip_address="10.0.0.7", organization_id="acme")
    agent = Agent(
        TestModel(call_tools=["ping"]),
        toolsets=[FunctionToolset([ping])],
        capabilities=[capability],
    )
    await agent.run("ping")
    event = audit.events[0]
    assert event.ip_address == "10.0.0.7"
    assert event.organization_id == "acme"


async def test_the_runs_deps_supply_the_ip() -> None:
    """The per-run IP wins, so one agent can serve requests from many clients.

    Reading it only off the constructor is what forced a fresh agent per
    request — and the failure mode if a transport builds once anyway is silent:
    every record carries the IP of whoever arrived first.
    """
    audit = _CapturingLogger()
    agent = _pinging_agent(AuditCapability(audit, ip_address="10.0.0.7"))

    await agent.run("ping", deps=AgentDeps(ip_address="203.0.113.9"))
    await agent.run("ping", deps=AgentDeps(ip_address="198.51.100.4"))

    assert [e.ip_address for e in audit.events] == ["203.0.113.9", "198.51.100.4"]


async def test_a_run_with_no_ip_falls_back_to_the_constructed_one() -> None:
    audit = _CapturingLogger()
    agent = _pinging_agent(AuditCapability(audit, ip_address="10.0.0.7"))

    await agent.run("ping", deps=AgentDeps())

    assert audit.events[0].ip_address == "10.0.0.7"


async def test_deps_without_an_ip_field_are_not_an_error() -> None:
    """The deps type is the host's to choose — a project's own class need not
    carry the field, and `None` deps are what pydantic-ai passes by default."""
    audit = _CapturingLogger()
    agent = _pinging_agent(AuditCapability(audit, ip_address="10.0.0.7"))

    await agent.run("ping", deps=object())

    assert audit.events[0].ip_address == "10.0.0.7"


def _pinging_agent(capability: AuditCapability) -> Agent[Any, Any]:
    def ping() -> str:
        """Ping."""
        return "pong"

    return Agent(
        TestModel(call_tools=["ping"]),
        toolsets=[FunctionToolset([ping])],
        capabilities=[capability],
    )


async def test_failure_is_recorded_and_reraised() -> None:
    def boom() -> str:
        """Always explodes."""
        raise ValueError("kaboom")

    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["boom"]),
        toolsets=[FunctionToolset([boom])],
        capabilities=[AuditCapability(audit)],
    )
    with pytest.raises(ValueError, match="kaboom"):
        await agent.run("boom")
    failures = [e for e in audit.events if not e.success]
    assert failures
    assert "kaboom" in (failures[0].error or "")


async def test_raising_sink_never_breaks_the_run(caplog: pytest.LogCaptureFixture) -> None:
    # Non-raising semantics: a broken sink degrades to a logged error and a
    # dropped audit record — the tool result still reaches the model.
    def ping() -> str:
        """Ping."""
        return "pong"

    agent = Agent(
        TestModel(call_tools=["ping"]),
        toolsets=[FunctionToolset([ping])],
        capabilities=[AuditCapability(_RaisingLogger())],
    )
    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.audit"):
        result = await agent.run("ping")
    assert result.output is not None
    assert any("event dropped" in record.message for record in caplog.records)
