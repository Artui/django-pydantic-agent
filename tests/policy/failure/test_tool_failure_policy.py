from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.test import TestModel

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.failure.tool_failure_policy import ToolFailurePolicy
from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any) -> None:
        self.events.append(event)


def _raising_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg)
    def boom(target: str) -> str:
        """Raise on purpose."""
        raise RuntimeError("credentials=hunter2")

    return reg


def _agent(
    *,
    tool_failure: ToolFailureConfig | None = None,
    audit: Any = None,
) -> Agent[AgentDeps, Any]:
    config = AgentConfig(
        model=TestModel(call_tools=["boom"]),
        audit_logger=audit,
        tool_failure=tool_failure if tool_failure is not None else ToolFailureConfig(),
    )
    return build_agent(_raising_registry(), config)


def _tool_returns(result: Any) -> list[Any]:
    return [
        part
        for message in result.all_messages()
        for part in message.parts
        if type(part).__name__ == "ToolReturnPart"
    ]


async def test_a_raising_tool_no_longer_ends_the_run() -> None:
    """The behaviour the policy exists to replace.

    Without it the exception propagates, the transport emits ``RUN_ERROR``, and
    everything else the turn produced goes with it.
    """
    result = await _agent().run("go", deps=AgentDeps())

    returns = _tool_returns(result)
    assert len(returns) == 1
    assert returns[0].outcome == "failed"
    assert "boom" in returns[0].content


async def test_the_default_message_does_not_carry_the_exception_text() -> None:
    # An exception message is written for an operator. Anything handed to the
    # model is also handed to whatever renders the transcript.
    result = await _agent().run("go", deps=AgentDeps())

    assert "hunter2" not in _tool_returns(result)[0].content


async def test_include_detail_opts_into_the_exception_text() -> None:
    result = await _agent(tool_failure=ToolFailureConfig(include_detail=True)).run(
        "go", deps=AgentDeps()
    )

    content = _tool_returns(result)[0].content
    assert "RuntimeError" in content
    assert "hunter2" in content


async def test_disabled_restores_the_failing_run() -> None:
    with pytest.raises(RuntimeError, match="hunter2"):
        await _agent(tool_failure=ToolFailureConfig(enabled=False)).run("go", deps=AgentDeps())


async def test_the_operator_copy_is_never_redacted() -> None:
    """Audit still records the real failure against the tool that caused it.

    The two capabilities ride different hooks, so neither has to be ordered
    against the other for this to hold.
    """
    audit = _RecordingAudit()

    await _agent(audit=audit).run("go", deps=AgentDeps())

    failures = [e for e in audit.events if not e.success]
    assert len(failures) == 1
    assert failures[0].tool_name == "boom"
    assert "hunter2" in (failures[0].error or "")


async def test_the_failure_is_logged_with_its_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"):
        await _agent().run("go", deps=AgentDeps())

    assert any(record.exc_info is not None for record in caplog.records)


async def test_a_model_retry_still_reaches_the_model_as_a_retry() -> None:
    """Control flow must pass through untouched.

    ``ModelRetry`` surfaces as ``ToolRetryError``, which Pydantic-AI does not
    route to ``on_tool_execute_error`` -- an ``except Exception`` around the
    handler would have converted it into a terminal failure and silently
    removed the retry contract.
    """
    reg = ToolRegistry()
    calls: list[int] = []

    @tool(reg)
    def flaky(target: str) -> str:
        """Fail once, then succeed."""
        calls.append(1)
        if len(calls) == 1:
            raise ModelRetry("try again")
        return "ok"

    agent = build_agent(reg, AgentConfig(model=TestModel(call_tools=["flaky"])))

    await agent.run("go", deps=AgentDeps())

    assert len(calls) == 2


def test_the_policy_can_be_built_without_a_config() -> None:
    # Composed directly rather than through ``build_agent``: the default record
    # is the same either way.
    assert isinstance(ToolFailurePolicy(), ToolFailurePolicy)
