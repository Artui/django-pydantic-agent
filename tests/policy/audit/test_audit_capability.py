from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
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

    await agent.run("ping", deps=AgentDeps(user=None, ip_address="203.0.113.9"))
    await agent.run("ping", deps=AgentDeps(user=None, ip_address="198.51.100.4"))

    assert [e.ip_address for e in audit.events] == ["203.0.113.9", "198.51.100.4"]


async def test_a_run_with_no_ip_falls_back_to_the_constructed_one() -> None:
    audit = _CapturingLogger()
    agent = _pinging_agent(AuditCapability(audit, ip_address="10.0.0.7"))

    await agent.run("ping", deps=AgentDeps(user=None))

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


async def _record_failure(
    handler: Callable[[dict[str, Any]], Awaitable[Any]],
) -> tuple[AuditEvent, BaseException]:
    """Drive ``wrap_tool_execute`` directly with a handler that raises.

    Direct, rather than through an agent, so the exception the wrapper is
    handed is exactly the one under test whichever pydantic-ai hook order is
    installed: from 2.54 the ``on_tool_execute_error`` hook runs *inside* the
    wrapper's handler, and before that it ran outside it.
    """
    audit = _CapturingLogger()
    with pytest.raises(Exception) as raised:
        await AuditCapability(audit).wrap_tool_execute(
            # Only ``deps`` is read off the context, for the IP fallback.
            SimpleNamespace(deps=None),
            call=ToolCallPart(tool_name="boom", args={}),
            tool_def=ToolDefinition(name="boom", parameters_json_schema={"type": "object"}),
            args={},
            handler=handler,
        )
    [event] = audit.events
    return event, raised.value


async def test_a_translated_failure_records_the_exception_it_was_raised_from() -> None:
    """A capability that turns a tool's exception into ``ToolFailed`` from
    ``on_tool_execute_error`` hands the wrapper its *translation* once that hook
    runs inside ``wrap_tool_execute``. The operator's record names the exception
    the translation was raised from, not the model-facing text."""

    async def handler(args: dict[str, Any]) -> Any:
        raise ToolFailed("The boom tool failed and returned no result.") from RuntimeError("kaboom")

    event, raised = await _record_failure(handler)

    assert event.success is False
    assert event.error == "RuntimeError: kaboom"
    # Unwrapping is for the record only: the model still gets the failed result.
    assert isinstance(raised, ToolFailed)


@pytest.mark.parametrize("suppress", [False, True], ids=["no-cause", "from-none"])
async def test_a_tool_failed_with_no_cause_records_its_own_text(suppress: bool) -> None:
    """Nothing to unwrap: a bare ``ToolFailed``, or one raised ``from None`` to
    hide its cause on purpose, is recorded as itself."""

    async def handler(args: dict[str, Any]) -> Any:
        if suppress:
            raise ToolFailed("Order 42 does not exist.") from None
        raise ToolFailed("Order 42 does not exist.")

    event, _ = await _record_failure(handler)

    assert event.error == "ToolFailed: Order 42 does not exist."


async def test_only_a_tool_failed_is_unwrapped() -> None:
    """Any other exception carrying a cause is the failure itself, and is
    recorded as raised. Its text is not written for the model, so there is
    nothing to see past."""

    async def handler(args: dict[str, Any]) -> Any:
        raise ValueError("outer") from KeyError("inner")

    event, _ = await _record_failure(handler)

    assert event.error == "ValueError: outer"


async def test_a_tool_raising_tool_failed_records_its_own_message() -> None:
    """A tool's own ``raise ToolFailed(...) from error`` is not unwrapped: the
    tool chose that message as its outcome, and the record keeps it.

    pydantic-ai turns a tool's ``ToolFailed`` into a ``ToolFailedError`` (raised
    from it) inside the execution step, before any wrapper sees it, and never
    routes it to ``on_tool_execute_error``. So this arrives as a different type
    from a hook's translation, under either hook order, and the description
    rule never applies to it.
    """

    def lookup() -> str:
        """Look up order 42."""
        raise ToolFailed("Order 42 does not exist.") from LookupError("no row with pk=42")

    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["lookup"]),
        toolsets=[FunctionToolset([lookup])],
        capabilities=[AuditCapability(audit)],
    )
    await agent.run("lookup")

    failures = [e for e in audit.events if not e.success]
    assert [e.error for e in failures] == ["ToolFailedError: Order 42 does not exist."]
