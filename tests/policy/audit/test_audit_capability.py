from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import pytest
from pydantic_ai import (
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    ModelRetry,
    RunContext,
    Tool,
)
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, SkipToolExecution, ToolFailed
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness import CodeMode
from pydantic_ai_harness import guardrails as harness_guardrails
from rest_framework.permissions import AllowAny
from rest_framework_pydantic_ai import SpecCapability
from rest_framework_services import ServiceSpec

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.audit.audit_capability import AuditCapability
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.failure.tool_failure_policy import ToolFailurePolicy
from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry

# Every scenario below is a real agent run: a real ``Agent``, real capabilities
# and pydantic-ai's own hook dispatch. Which hook encloses which is the
# installed pydantic-ai's decision (2.54 moved every ``before`` / ``after`` /
# ``on_error`` hook inside ``wrap_tool_execute``), so a hand-driven hook would
# only restate this file's assumption about it. The suite runs on both orders,
# and every record asserted here is the same on both, save where a capability
# sorts after audit, whose tests say which record each order gets.

_HOOKS_INSIDE_THE_WRAPPER = tuple(
    int(part) for part in version("pydantic-ai-slim").split(".")[:2]
) >= (2, 54)


class _CapturingLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class _RaisingLogger:
    def record(self, event: AuditEvent) -> None:
        raise RuntimeError("sink down")


def _records(sink: _CapturingLogger) -> list[tuple[str, str, bool, str | None, int | None]]:
    """Each record as ``(tool, arguments, success, error, result_size)``."""
    return [(e.tool_name, e.arguments_repr, e.success, e.error, e.result_size) for e in sink.events]


def _agent(
    *tools: Callable[..., Any],
    sink: _CapturingLogger,
    capabilities: Sequence[AbstractCapability[Any]] = (),
    tool_failure: bool = True,
    model: Any = None,
    tool_guard: ToolGuardConfig | None = None,
    toolsets: Sequence[Any] = (),
) -> Agent[AgentDeps, Any]:
    """An agent composed the way a transport composes one: through ``build_agent``.

    That is what puts audit after every capability in ``config.capabilities``
    and the failure policy where ``build_agent`` places it, so the hook order
    under test is the one a deployment gets.
    """
    registry = ToolRegistry()
    for fn in tools:
        tool(registry)(fn)
    return build_agent(
        registry,
        AgentConfig(
            model=model if model is not None else TestModel(call_tools=[t.__name__ for t in tools]),
            audit_logger=sink,
            capabilities=list(capabilities),
            tool_failure=ToolFailureConfig(enabled=tool_failure),
            tool_guard=tool_guard,
            toolsets=list(toolsets),
        ),
    )


def _deps(ip_address: str | None = None) -> AgentDeps:
    return AgentDeps(user=None, ip_address=ip_address)


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
    ]


def _scripted(*rounds: list[ToolCallPart]) -> FunctionModel:
    """A model that sends each round of tool calls in turn, then answers."""
    remaining = list(rounds)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if remaining:
            return ModelResponse(parts=list(remaining.pop(0)))
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(respond)


# Capabilities shaped like pydantic-ai-harness's: a guardrail that vetoes with
# ``SkipToolExecution``, an error hook that recovers, an argument rewrite and a
# result rewrite (``ToolOutputLimits``-style). None declares an ordering, so
# each sits where a project's own capability would.


class _Veto(AbstractCapability[Any]):
    """Refuses a call from ``before_tool_execute``.

    ``tools`` limits it to those tool names, and ``allow`` lets that many of
    their calls through first, the way a budget guardrail runs out.
    """

    def __init__(self, tools: frozenset[str] | None = None, allow: int = 0) -> None:
        self._tools = tools
        self._allow = allow
        self._seen = 0

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if self._tools is not None and tool_def.name not in self._tools:
            return args
        self._seen += 1
        if self._seen > self._allow:
            raise SkipToolExecution("Blocked by the workspace guardrail.")
        return args


class _InnermostVeto(_Veto):
    """``_Veto`` pinned innermost, where harness pins its guardrail and judge."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")


class _RetryFirstCall(AbstractCapability[Any]):
    """Asks the model to try again from ``before_tool_execute``, once.

    A harness-style argument check does this: the call is refused before the
    tool runs, with a ``ModelRetry`` rather than a veto.
    """

    def __init__(self) -> None:
        self._asked = False

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._asked:
            self._asked = True
            raise ModelRetry("n must be positive")
        return args


class _RetryOnce(AbstractCapability[Any]):
    """A wrapper that runs the tool again when it raises.

    pydantic-ai allows it: each call of ``handler`` runs the tool again, and
    from 2.54 every capability's ``before_tool_execute`` with it.
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
    ) -> Any:
        try:
            return await handler(args)
        except ValueError:
            return await handler(args)


class _WrapRewriteArgs(AbstractCapability[Any]):
    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
    ) -> Any:
        return await handler({**args, "n": 5})


class _Recover(AbstractCapability[Any]):
    def __init__(self) -> None:
        self.seen: list[Exception] = []

    async def on_tool_execute_error(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        error: Exception,
    ) -> Any:
        self.seen.append(error)
        return "recovered"


class _RewriteArgs(AbstractCapability[Any]):
    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        return {**args, "n": 7}


class _InnermostRewriteArgs(_RewriteArgs):
    """``_RewriteArgs`` pinned innermost, the tier audit shares."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")


class _RewriteResult(AbstractCapability[Any]):
    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        return "y" * 500


class _SlowHooks(AbstractCapability[Any]):
    """A wrapper, a ``before`` and an ``after``, each far slower than the tool.

    Before 2.54 the wrapper runs between the ``before`` hooks and the tool, so
    it is what shows that audit starts the clock at the later of the two.
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
    ) -> Any:
        await asyncio.sleep(0.3)
        result = await handler(args)
        await asyncio.sleep(0.3)
        return result

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return args

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        await asyncio.sleep(0.3)
        return result


def test_audit_declares_innermost_ordering() -> None:
    """Innermost, so its ``before_tool_execute`` runs after every other one and
    its ``on_tool_execute_error`` and ``after_tool_execute`` before every other
    one. Those are the two moments either side of the tool itself."""
    ordering = AuditCapability(_CapturingLogger()).get_ordering()
    assert ordering is not None
    assert ordering.position == "innermost"


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
    assert _records(audit) == [("triple", '{"n": 0}', True, None, 1)]


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

    await agent.run("ping", deps=_deps("203.0.113.9"))
    await agent.run("ping", deps=_deps("198.51.100.4"))

    assert [e.ip_address for e in audit.events] == ["203.0.113.9", "198.51.100.4"]


async def test_a_run_with_no_ip_falls_back_to_the_constructed_one() -> None:
    audit = _CapturingLogger()
    agent = _pinging_agent(AuditCapability(audit, ip_address="10.0.0.7"))

    await agent.run("ping", deps=_deps())

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


@pytest.mark.parametrize("tool_failure", [False, True], ids=["policy-off", "policy-on"])
async def test_a_raising_tool_is_recorded_by_its_own_exception(tool_failure: bool) -> None:
    """The record names the tool's exception, whichever capability converts it.

    With the failure policy on, the exception the run continues past is the
    policy's ``ToolFailed``, written for the model and redacted by default.
    The operator's record still reads the tool's own exception.
    """

    def lookup(n: int) -> str:
        """Look a thing up."""
        raise ValueError("kaboom")

    audit = _CapturingLogger()
    agent = _agent(lookup, sink=audit, tool_failure=tool_failure)

    if tool_failure:
        result = await agent.run("go", deps=_deps())
        # The record is the operator's copy only: the model still gets the
        # policy's failed result, which carries none of the exception's text.
        [returned] = _tool_returns(result.all_messages())
        assert returned.outcome == "failed"
        assert "kaboom" not in str(returned.content)
    else:
        with pytest.raises(ValueError, match="kaboom"):
            await agent.run("go", deps=_deps())

    assert _records(audit) == [("lookup", '{"n": 0}', False, "ValueError: kaboom", None)]


async def test_a_tools_own_tool_failed_is_recorded_as_it_propagated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``ToolFailed`` the tool raises skips the error hooks.

    pydantic-ai converts it into a ``ToolFailedError`` carrying the same
    message before any capability hook sees it, so that is what reaches audit
    and what it records, never the ``ToolFailed``'s cause. The failure policy
    is not handed it either.
    """

    def lookup(n: int) -> str:
        """Fails with a sentence for the model."""
        try:
            raise ValueError("root cause")
        except ValueError as error:
            raise ToolFailed("model copy") from error

    audit = _CapturingLogger()
    agent = _agent(lookup, sink=audit)
    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"):
        await agent.run("go", deps=_deps())

    assert _records(audit) == [("lookup", '{"n": 0}', False, "ToolFailedError: model copy", None)]
    # The policy logs every failure it converts; silence is it never seeing one.
    assert not [r for r in caplog.records if r.name == "django_pydantic_agent.failure"]


async def test_a_model_retry_is_recorded_as_it_propagated() -> None:
    """A retry skips the error hooks too, and is one record per attempt."""

    def lookup(n: int) -> str:
        """Look a thing up."""
        if n == 0:
            raise ModelRetry("n must be positive")
        return "found"

    audit = _CapturingLogger()
    model = _scripted(
        [ToolCallPart(tool_name="lookup", args={"n": 0}, tool_call_id="first")],
        [ToolCallPart(tool_name="lookup", args={"n": 1}, tool_call_id="second")],
    )
    await _agent(lookup, sink=audit, model=model).run("go", deps=_deps())

    assert _records(audit) == [
        ("lookup", '{"n": 0}', False, "ToolRetryError: n must be positive", None),
        ("lookup", '{"n": 1}', True, None, 5),
    ]


async def test_a_recovered_failure_is_still_recorded_as_the_failure() -> None:
    """Another capability recovering is a decision about the run, not the tool.

    The model gets the recovered value; the record keeps what the tool did.
    """

    def lookup(n: int) -> str:
        """Look a thing up."""
        raise ValueError("kaboom")

    audit = _CapturingLogger()
    recover = _Recover()
    result = await _agent(lookup, sink=audit, capabilities=[recover]).run("go", deps=_deps())

    assert _records(audit) == [("lookup", '{"n": 0}', False, "ValueError: kaboom", None)]
    assert [part.content for part in _tool_returns(result.all_messages())] == ["recovered"]


@pytest.mark.parametrize("veto", [_Veto, _InnermostVeto], ids=["unpinned", "innermost"])
async def test_a_vetoed_call_is_not_recorded(veto: type[_Veto]) -> None:
    """A call another capability stops before it reaches the tool has no record.

    That is what harness's guardrails and tool-call judge do, by raising
    ``SkipToolExecution`` from ``before_tool_execute``, and it matches how a
    destructive call the tool guard holds for approval has none. Harness pins
    both innermost, the tier audit shares, so that case is here as well as an
    unpinned one.
    """
    calls: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        calls.append(n)
        return "found"

    audit = _CapturingLogger()
    result = await _agent(lookup, sink=audit, capabilities=[veto()]).run("go", deps=_deps())

    assert calls == []
    assert _records(audit) == []
    assert [part.content for part in _tool_returns(result.all_messages())] == [
        "Blocked by the workspace guardrail."
    ]


async def test_audit_composed_ahead_of_an_innermost_veto_does_not_record_it() -> None:
    """Composed by hand ahead of an innermost guardrail, audit is not the last
    ``before_tool_execute``, and from pydantic-ai 2.54, where its wrapper
    encloses the guardrail's, the veto reaches it after audit has seen the call
    start. The vetoed call is still not recorded, because a
    ``SkipToolExecution`` is a call that did not execute. Earlier releases
    never enter the wrapper for it.
    """

    def lookup(n: int) -> str:
        """Look a thing up."""
        return "found"

    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["lookup"]),
        deps_type=AgentDeps,
        tools=[lookup],
        capabilities=[AuditCapability(audit), _InnermostVeto()],
    )
    await agent.run("go", deps=_deps())

    assert _records(audit) == []


async def test_a_veto_from_a_capability_passed_per_run_is_not_recorded() -> None:
    """A capability a transport passes to one run sorts after the agent's own,
    so an innermost one lands inside audit however ``build_agent`` composed it."""
    calls: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        calls.append(n)
        return "found"

    audit = _CapturingLogger()
    agent = _agent(lookup, sink=audit)
    async with agent.iter("go", deps=_deps(), capabilities=[_InnermostVeto()]) as run:
        async for _ in run:
            pass

    assert calls == []
    assert _records(audit) == []


class _SlowInnermostRewriteArgs(_InnermostRewriteArgs):
    """``_InnermostRewriteArgs`` taking far longer than the tool to do it."""

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return await super().before_tool_execute(ctx, call=call, tool_def=tool_def, args=args)


async def test_a_per_run_innermost_rewrite_reaches_the_record_only_before_2_54() -> None:
    """A ``before_tool_execute`` sorted after audit, from 2.54.

    A capability passed to a single run sorts after audit within the innermost
    tier, so its ``before_tool_execute`` runs after audit's. Before 2.54
    audit's wrapper is entered after every ``before`` hook, and records the
    rewritten arguments and times the tool alone; from 2.54 the wrapper
    encloses that hook, and the record keeps the arguments from before the
    rewrite and times the hook with the tool. The rest of what such a
    capability changes is below.
    """
    received: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        received.append(n)
        return "found"

    audit = _CapturingLogger()
    agent = _agent(lookup, sink=audit)
    capabilities = [_SlowInnermostRewriteArgs()]
    async with agent.iter("go", deps=_deps(), capabilities=capabilities) as run:
        async for _ in run:
            pass

    assert received == [7]
    assert _records(audit) == [
        ("lookup", '{"n": 0}' if _HOOKS_INSIDE_THE_WRAPPER else '{"n": 7}', True, None, 5)
    ]
    assert (audit.events[0].duration_ms >= 300) is _HOOKS_INSIDE_THE_WRAPPER


class _OneThing(AbstractCapability[Any]):
    """An innermost capability doing the one thing ``behaviour`` names to a call.

    Every hook ``behaviour`` does not name passes the call on unchanged, so
    each case below differs from the tool's own execution in one way only.
    Passed to a single run it sorts after the agent's own capabilities, audit
    included; in ``config.capabilities`` ``build_agent`` puts audit after it,
    unless ``inside_audit`` makes its ordering ask audit to wrap it.
    """

    def __init__(self, behaviour: str, *, inside_audit: bool = False) -> None:
        self._behaviour = behaviour
        self._inside_audit = inside_audit
        self._asked = False

    def get_ordering(self) -> CapabilityOrdering:
        if self._inside_audit:
            return CapabilityOrdering(position="innermost", wrapped_by=[AuditCapability])
        return CapabilityOrdering(position="innermost")

    def _ask_again_once(self, behaviour: str) -> None:
        if self._behaviour == behaviour and not self._asked:
            self._asked = True
            raise ModelRetry("n must be positive")

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        self._ask_again_once("before-asks-again")
        return args

    async def on_tool_execute_error(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        error: Exception,
    ) -> Any:
        if self._behaviour == "on-error-recovers":
            return "recovered"
        if self._behaviour == "on-error-raises-its-own":
            raise RuntimeError("converted") from error
        raise error

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        self._ask_again_once("after-asks-again")
        if self._behaviour == "after-rewrites-the-result":
            return "y" * 500
        if self._behaviour == "after-is-slow":
            await asyncio.sleep(0.3)
        return result

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
    ) -> Any:
        self._ask_again_once("wrapper-asks-again")
        if self._behaviour == "wrapper-rewrites-the-arguments":
            args = {**args, "n": 5}
        if self._behaviour == "wrapper-is-slow":
            await asyncio.sleep(0.3)
        try:
            result = await handler(args)
        except Exception as error:
            # From 2.54 what reaches a wrapper is the failure policy's copy,
            # not the tool's exception, so each of these acts on any one.
            if self._behaviour == "wrapper-reruns":
                return await self._run_again(handler, args)
            if self._behaviour == "wrapper-recovers":
                return "recovered"
            if self._behaviour == "wrapper-raises-its-own":
                # A ToolFailed rather than an error: from 2.54 a wrapper is
                # outside every error hook, so an error raised here would end
                # the run rather than reach the failure policy.
                raise ToolFailed("converted") from error
            raise
        if self._behaviour == "wrapper-reruns-a-success":
            return await self._run_again(handler, args)
        if self._behaviour == "wrapper-rewrites-the-result":
            return "y" * 500
        return result

    async def _run_again(
        self, handler: Callable[[dict[str, Any]], Any], args: dict[str, Any]
    ) -> Any:
        """Run the tool a second time, later and with other arguments.

        Different arguments say which run a record's ``arguments_repr`` comes
        from, and the pause between the runs says whether its ``duration_ms``
        spans both.
        """
        await asyncio.sleep(0.3)
        return await handler({**args, "n": 5})


_FAILED = ("lookup", '{"n": 0}', False, "ValueError: kaboom", None)
_FOUND = ("lookup", '{"n": 0}', True, None, 5)
_ASKED_AGAIN = ("lookup", '{"n": 0}', False, "ModelRetry: n must be positive", None)
# The tool's result on any run after its first, so a record says which run it
# measured: called again with the same arguments, and run again by a wrapper
# with other ones.
_FOUND_AGAIN = ("lookup", '{"n": 0}', True, None, 11)
_RUN_AGAIN = ("lookup", '{"n": 5}', True, None, 11)


@dataclass(frozen=True)
class _SortedAfterAudit:
    """What one ``_OneThing`` behaviour does to the record, on each hook order.

    ``own`` is the tool's own execution, which is what the record holds when
    the capability is composed through ``config.capabilities``, on both.
    ``from_2_54`` and ``before_2_54`` are what it holds when the capability
    sorts after audit: passed to a single run, or in ``config.capabilities``
    with an ordering that places it inside audit.
    """

    behaviour: str
    tool_fails: bool
    ran: list[int]
    own: list[Any]
    from_2_54: list[Any]
    before_2_54: list[Any]
    slow_from_2_54: bool = False
    slow_before_2_54: bool = False
    # What the tool raises on its first run when it fails.
    raises: type[Exception] = ValueError

    @property
    def id(self) -> str:
        """The behaviour, suffixed with what the tool raises where that is not
        the ``ValueError`` most rows fail with."""
        if self.raises is ValueError:
            return self.behaviour
        return f"{self.behaviour}-on-{self.raises.__name__}"


def _past_every_hook(raises: type[Exception], recorded_as: str) -> list[_SortedAfterAudit]:
    """A wrapper recovering, raising its own and rerunning, when the tool raises
    one of the two exceptions pydantic-ai routes past every error and result
    hook.

    ``recorded_as`` is the type pydantic-ai converts ``raises`` into, which is
    what the record names when audit sees it first. Sorted after audit, from
    2.54 too, audit's hooks never settle the call, so audit's wrapper records
    whatever the wrapper inside it hands back: a recovery as a success, its
    own exception in the tool's place, and a rerun as the later run alone,
    the first having no record. Before 2.54 each is recorded as for any other
    failure.
    """
    failed = ("lookup", '{"n": 0}', False, f"{recorded_as}: kaboom", None)
    recovered = ("lookup", '{"n": 0}', True, None, 9)
    converted = ("lookup", '{"n": 0}', False, "ToolFailed: converted", None)
    return [
        _SortedAfterAudit(
            "wrapper-recovers",
            tool_fails=True,
            raises=raises,
            ran=[0],
            own=[failed],
            from_2_54=[recovered],
            before_2_54=[recovered],
        ),
        _SortedAfterAudit(
            "wrapper-raises-its-own",
            tool_fails=True,
            raises=raises,
            ran=[0],
            own=[failed],
            from_2_54=[converted],
            before_2_54=[converted],
        ),
        _SortedAfterAudit(
            "wrapper-reruns",
            tool_fails=True,
            raises=raises,
            ran=[0, 5],
            own=[failed, _RUN_AGAIN],
            from_2_54=[_RUN_AGAIN],
            before_2_54=[_FOUND_AGAIN],
            slow_before_2_54=True,
        ),
    ]


_SORTED_AFTER_AUDIT = [
    # From 2.54 audit's wrapper encloses every hook, and these hooks run
    # between audit's and the tool.
    _SortedAfterAudit(
        "on-error-recovers",
        tool_fails=True,
        ran=[0],
        own=[_FAILED],
        from_2_54=[("lookup", '{"n": 0}', True, None, 9)],
        before_2_54=[_FAILED],
    ),
    _SortedAfterAudit(
        "on-error-raises-its-own",
        tool_fails=True,
        ran=[0],
        own=[_FAILED],
        from_2_54=[("lookup", '{"n": 0}', False, "RuntimeError: converted", None)],
        before_2_54=[_FAILED],
    ),
    _SortedAfterAudit(
        "after-rewrites-the-result",
        tool_fails=False,
        ran=[0],
        own=[_FOUND],
        from_2_54=[("lookup", '{"n": 0}', True, None, 500)],
        before_2_54=[_FOUND],
    ),
    _SortedAfterAudit(
        "after-is-slow",
        tool_fails=False,
        ran=[0],
        own=[_FOUND],
        from_2_54=[_FOUND],
        before_2_54=[_FOUND],
        slow_from_2_54=True,
    ),
    # The refused attempt never ran the tool, yet from 2.54 it is recorded.
    _SortedAfterAudit(
        "before-asks-again",
        tool_fails=False,
        ran=[0],
        own=[_FOUND],
        from_2_54=[_ASKED_AGAIN, _FOUND],
        before_2_54=[_FOUND],
    ),
    # The tool succeeded and its result was refused, yet from 2.54 that call
    # is recorded as a failure. The model's retry runs the tool again.
    _SortedAfterAudit(
        "after-asks-again",
        tool_fails=False,
        ran=[0, 0],
        own=[_FOUND, _FOUND_AGAIN],
        from_2_54=[_ASKED_AGAIN, _FOUND_AGAIN],
        before_2_54=[_FOUND, _FOUND_AGAIN],
    ),
    # Before 2.54 the hooks run around the wrappers, and this wrapper sits
    # between audit's wrapper and the tool.
    _SortedAfterAudit(
        "wrapper-rewrites-the-arguments",
        tool_fails=False,
        ran=[5],
        own=[("lookup", '{"n": 5}', True, None, 5)],
        from_2_54=[("lookup", '{"n": 5}', True, None, 5)],
        before_2_54=[_FOUND],
    ),
    _SortedAfterAudit(
        "wrapper-recovers",
        tool_fails=True,
        ran=[0],
        own=[_FAILED],
        from_2_54=[_FAILED],
        before_2_54=[("lookup", '{"n": 0}', True, None, 9)],
    ),
    _SortedAfterAudit(
        "wrapper-raises-its-own",
        tool_fails=True,
        ran=[0],
        own=[_FAILED],
        from_2_54=[_FAILED],
        before_2_54=[("lookup", '{"n": 0}', False, "ToolFailed: converted", None)],
    ),
    _SortedAfterAudit(
        "wrapper-rewrites-the-result",
        tool_fails=False,
        ran=[0],
        own=[_FOUND],
        from_2_54=[_FOUND],
        before_2_54=[("lookup", '{"n": 0}', True, None, 500)],
    ),
    _SortedAfterAudit(
        "wrapper-is-slow",
        tool_fails=False,
        ran=[0],
        own=[_FOUND],
        from_2_54=[_FOUND],
        before_2_54=[_FOUND],
        slow_before_2_54=True,
    ),
    _SortedAfterAudit(
        "wrapper-asks-again",
        tool_fails=False,
        ran=[0],
        own=[_FOUND],
        from_2_54=[_FOUND],
        before_2_54=[_ASKED_AGAIN, _FOUND],
    ),
    # Run again inside audit's one wrapper entry, after an ordinary failure or
    # after a success, the tool gets one record on both orders. From 2.54
    # audit's hooks settled it on the first run, and it describes that run:
    # its arguments, its outcome and its time. Before 2.54 audit's wrapper saw
    # only the last run's outcome, with the arguments it passed on, and timed
    # both runs and the pause between them.
    _SortedAfterAudit(
        "wrapper-reruns",
        tool_fails=True,
        ran=[0, 5],
        own=[_FAILED, _RUN_AGAIN],
        from_2_54=[_FAILED],
        before_2_54=[_FOUND_AGAIN],
        slow_before_2_54=True,
    ),
    _SortedAfterAudit(
        "wrapper-reruns-a-success",
        tool_fails=False,
        ran=[0, 5],
        own=[_FOUND, _RUN_AGAIN],
        from_2_54=[_FOUND],
        before_2_54=[_FOUND_AGAIN],
        slow_before_2_54=True,
    ),
    *_past_every_hook(ModelRetry, "ToolRetryError"),
    *_past_every_hook(ToolFailed, "ToolFailedError"),
]


@pytest.mark.parametrize("composed", ["per-run", "config", "config-ordered-inside-audit"])
@pytest.mark.parametrize("case", _SORTED_AFTER_AUDIT, ids=[case.id for case in _SORTED_AFTER_AUDIT])
async def test_a_capability_sorted_after_audit_reaches_the_record(
    case: _SortedAfterAudit, composed: str
) -> None:
    """A capability that sorts after audit runs between audit and the tool.

    Passed to a single run, an innermost capability sorts after audit, so what
    it does to the call reaches the record as if the tool had done it: from
    2.54 what its ``before``, ``on_error`` and ``after`` hooks do, the reruns
    of its wrapper, and what its wrapper does with a tool's own
    ``ModelRetry`` or ``ToolFailed``; before 2.54 everything its wrapper does.
    These are examples of that, not a list of the only ways. The same
    capability in ``config.capabilities`` leaves the record the tool's own on
    both orders, because ``build_agent`` appends audit after it. That is list
    order, which a capability's own ordering overrides: in
    ``config.capabilities`` and asking audit to wrap it, it sorts after audit
    and reaches the record exactly as it does passed to a single run.

    A duration is never negative, whichever run a record describes.
    """
    ran: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        ran.append(n)
        if case.tool_fails and len(ran) == 1:
            raise case.raises("kaboom")
        return "found" if len(ran) == 1 else "found again"

    audit = _CapturingLogger()
    capability = _OneThing(case.behaviour, inside_audit=composed == "config-ordered-inside-audit")
    if composed == "per-run":
        agent = _agent(lookup, sink=audit)
        async with agent.iter("go", deps=_deps(), capabilities=[capability]) as run:
            async for _ in run:
                pass
    else:
        await _agent(lookup, sink=audit, capabilities=[capability]).run("go", deps=_deps())
    if composed == "config":
        expected, slow = case.own, False
    else:
        expected = case.from_2_54 if _HOOKS_INSIDE_THE_WRAPPER else case.before_2_54
        slow = case.slow_from_2_54 if _HOOKS_INSIDE_THE_WRAPPER else case.slow_before_2_54

    assert ran == case.ran
    assert _records(audit) == expected
    assert min(event.duration_ms for event in audit.events) >= 0
    assert (max(event.duration_ms for event in audit.events) >= 300) is slow


async def test_a_capability_composed_by_hand_after_audit_reaches_the_record_too() -> None:
    """Sorting after audit is what matters, not being passed to a single run:
    composed by hand after audit, an innermost recovery is recorded from 2.54."""

    def lookup(n: int) -> str:
        """Look a thing up."""
        raise ValueError("kaboom")

    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["lookup"]),
        deps_type=AgentDeps,
        tools=[lookup],
        capabilities=[AuditCapability(audit), _OneThing("on-error-recovers")],
    )
    await agent.run("go", deps=_deps())

    recovered = ("lookup", '{"n": 0}', True, None, 9)
    assert _records(audit) == [recovered if _HOOKS_INSIDE_THE_WRAPPER else _FAILED]


# The tool's own failure each ``replace-a-*`` verdict below screens: what the
# tool raises, the type pydantic-ai converts it into, and whether the model
# calls the tool again, as it does after a retry and not after a failure.
_SCREENS_A_FAILURE: dict[str, tuple[type[Exception], str, bool]] = {
    "replace-a-retry": (ModelRetry, "ToolRetryError", True),
    "replace-a-failure": (ToolFailed, "ToolFailedError", False),
}


@pytest.mark.skipif(
    not hasattr(harness_guardrails, "ToolGuardrail"),
    reason="this pydantic-ai-harness has no ToolGuardrail",
)
@pytest.mark.parametrize("composed", ["per-run", "config"])
@pytest.mark.parametrize("verdict", ["retry", "result-retry", "replace", *_SCREENS_A_FAILURE])
async def test_harness_tool_guardrail_is_innermost_and_sorts_after_audit_per_run(
    verdict: str, composed: str
) -> None:
    """pydantic-ai-harness's tool guardrail is innermost, so passed to a single
    run it sorts after audit and its verdicts reach the record from 2.54: a
    ``retry`` before the tool runs is recorded as a failure, a ``retry`` from
    its ``result_guard`` turns the tool's success into a failure, and a
    ``replace`` of the result is measured. Its ``result_guard`` also screens
    the message of a tool that raised ``ModelRetry`` or ``ToolFailed``, from
    its ``wrap_tool_execute``, so on every release a ``replace`` there is
    the message the record names. In ``config.capabilities`` none of it
    reaches the record."""

    async def guard(ctx: RunContext[Any], info: Any) -> Any:
        if not asked:
            asked.append(True)
            return harness_guardrails.GuardrailResult.retry("n must be positive")
        return harness_guardrails.GuardrailResult.allow()

    async def result_guard(ctx: RunContext[Any], info: Any) -> Any:
        if verdict == "replace":
            return harness_guardrails.GuardrailResult.replace("z" * 300)
        if not asked:
            asked.append(True)
            if verdict in _SCREENS_A_FAILURE:
                return harness_guardrails.GuardrailResult.replace("[redacted]")
            return harness_guardrails.GuardrailResult.retry("result rejected")
        return harness_guardrails.GuardrailResult.allow()

    asked: list[bool] = []
    guardrail = (
        harness_guardrails.ToolGuardrail(guard=guard)
        if verdict == "retry"
        else harness_guardrails.ToolGuardrail(result_guard=result_guard)
    )
    assert guardrail.get_ordering().position == "innermost"
    ran: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        ran.append(n)
        if verdict in _SCREENS_A_FAILURE and len(ran) == 1:
            raise _SCREENS_A_FAILURE[verdict][0]("secret 123")
        return "found"

    audit = _CapturingLogger()
    if composed == "per-run":
        agent = _agent(lookup, sink=audit)
        async with agent.iter("go", deps=_deps(), capabilities=[guardrail]) as run:
            async for _ in run:
                pass
    else:
        await _agent(lookup, sink=audit, capabilities=[guardrail]).run("go", deps=_deps())

    reaches_the_record = composed == "per-run" and _HOOKS_INSIDE_THE_WRAPPER
    if verdict == "retry":
        assert _records(audit) == ([_ASKED_AGAIN, _FOUND] if reaches_the_record else [_FOUND])
    elif verdict == "result-retry":
        rejected = ("lookup", '{"n": 0}', False, "ModelRetry: result rejected", None)
        assert _records(audit) == [rejected if reaches_the_record else _FOUND, _FOUND]
    elif verdict in _SCREENS_A_FAILURE:
        # The guardrail screens it in its wrapper, which per run sits inside
        # audit's on every release, and in config outside it.
        _, recorded_as, called_again = _SCREENS_A_FAILURE[verdict]
        message = "[redacted]" if composed == "per-run" else "secret 123"
        screened = ("lookup", '{"n": 0}', False, f"{recorded_as}: {message}", None)
        assert _records(audit) == ([screened, _FOUND] if called_again else [screened])
    else:
        replaced = ("lookup", '{"n": 0}', True, None, 300)
        assert _records(audit) == [replaced if reaches_the_record else _FOUND]


async def test_a_retry_asked_for_before_the_tool_runs_is_not_recorded() -> None:
    """The refused attempt never reached the tool, so only the retried one,
    which did, has a record."""
    calls: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        calls.append(n)
        return "found"

    audit = _CapturingLogger()
    model = _scripted(
        [ToolCallPart(tool_name="lookup", args={"n": 0}, tool_call_id="first")],
        [ToolCallPart(tool_name="lookup", args={"n": 1}, tool_call_id="second")],
    )
    agent = _agent(lookup, sink=audit, model=model, capabilities=[_RetryFirstCall()])
    await agent.run("go", deps=_deps())

    assert calls == [1]
    assert _records(audit) == [("lookup", '{"n": 1}', True, None, 5)]


def _wire_toolset() -> FunctionToolset[Any]:
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool_plain
    def wire(amount: int) -> str:
        """Wire money."""
        return f"wired {amount}"

    return toolset


async def _approve_and_resume(agent: Agent[AgentDeps, Any], deferred: Any) -> None:
    approvals = {call.tool_call_id: True for call in deferred.output.approvals}
    await agent.run(
        message_history=deferred.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals=approvals),
        deps=_deps(),
    )


async def test_a_call_an_approval_required_toolset_defers_is_recorded_once_it_runs() -> None:
    """pydantic-ai's own approval gate, ``FunctionToolset.approval_required()``,
    raises ``ApprovalRequired`` from inside the call, where audit sees it. The
    deferred call did not execute, so it has no record; the approved one does.
    """
    audit = _CapturingLogger()
    agent = _agent(
        sink=audit,
        model=TestModel(call_tools=["wire"]),
        toolsets=[_wire_toolset().approval_required()],
    )

    deferred = await agent.run("go", deps=_deps())
    assert isinstance(deferred.output, DeferredToolRequests)
    assert _records(audit) == []

    await _approve_and_resume(agent, deferred)
    assert _records(audit) == [("wire", '{"amount": 0}', True, None, 7)]


async def test_a_tool_asking_for_approval_itself_is_recorded_once_it_runs() -> None:
    calls: list[int] = []
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool
    def wire(ctx: RunContext[Any], amount: int) -> str:
        """Wire money."""
        if not ctx.tool_call_approved:
            raise ApprovalRequired
        calls.append(amount)
        return f"wired {amount}"

    audit = _CapturingLogger()
    agent = _agent(sink=audit, model=TestModel(call_tools=["wire"]), toolsets=[toolset])

    deferred = await agent.run("go", deps=_deps())
    assert isinstance(deferred.output, DeferredToolRequests)
    assert _records(audit) == []

    await _approve_and_resume(agent, deferred)
    assert calls == [0]
    assert _records(audit) == [("wire", '{"amount": 0}', True, None, 7)]


async def test_a_call_deferred_to_external_execution_has_no_record() -> None:
    """It never runs in this process: whatever executes it records it."""
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool_plain
    def external(amount: int) -> str:
        """Run elsewhere."""
        raise CallDeferred

    audit = _CapturingLogger()
    agent = _agent(sink=audit, model=TestModel(call_tools=["external"]), toolsets=[toolset])

    deferred = await agent.run("go", deps=_deps())

    assert isinstance(deferred.output, DeferredToolRequests)
    assert [call.tool_name for call in deferred.output.calls] == ["external"]
    assert _records(audit) == []


async def test_an_approval_deferral_is_not_recorded_until_the_approved_call_runs() -> None:
    def drop(n: int) -> str:
        """Delete a thing."""
        return "dropped"

    audit = _CapturingLogger()
    agent = _agent(
        drop,
        sink=audit,
        tool_guard=ToolGuardConfig(enabled=True, require_approval=frozenset({"drop"})),
    )

    deferred = await agent.run("go", deps=_deps())
    assert isinstance(deferred.output, DeferredToolRequests)
    assert _records(audit) == []

    approvals = {call.tool_call_id: True for call in deferred.output.approvals}
    await agent.run(
        message_history=deferred.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals=approvals),
        deps=_deps(),
    )
    assert _records(audit) == [("drop", '{"n": 0}', True, None, 7)]


@pytest.mark.parametrize(
    "rewrite", [_RewriteArgs, _InnermostRewriteArgs], ids=["unpinned", "innermost"]
)
async def test_the_arguments_are_the_ones_the_tool_received(rewrite: type[_RewriteArgs]) -> None:
    """Including an innermost capability's rewrite, which is why ``build_agent``
    appends audit after ``config.capabilities``: list order breaks ties within
    a tier, and that keeps audit's ``before_tool_execute`` the last to run."""
    received: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        received.append(n)
        return "found"

    audit = _CapturingLogger()
    await _agent(lookup, sink=audit, capabilities=[rewrite()]).run("go", deps=_deps())

    assert received == [7]
    assert _records(audit) == [("lookup", '{"n": 7}', True, None, 5)]


async def test_the_arguments_include_a_wrappers_rewrite() -> None:
    """Before 2.54 a wrapper rewrites the arguments after every ``before`` hook
    has run, so audit's ``before_tool_execute`` alone would miss it."""
    received: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        received.append(n)
        return "found"

    audit = _CapturingLogger()
    await _agent(lookup, sink=audit, capabilities=[_WrapRewriteArgs()]).run("go", deps=_deps())

    assert received == [5]
    assert _records(audit) == [("lookup", '{"n": 5}', True, None, 5)]


async def test_a_wrapper_that_runs_the_tool_twice_gets_one_record_per_run() -> None:
    """One record per time the tool actually ran, on either hook order.

    The wrapper retries a failure, and a budget guardrail lets one call
    through. From 2.54 the retry runs the guardrail again, which refuses it,
    so the tool runs once; before 2.54 the guardrail ran once, ahead of every
    wrapper, so the tool runs twice. The records follow the tool either way.
    """
    ran: list[str] = []

    def flaky(n: int) -> str:
        """Fails the first time."""
        if not ran:
            ran.append("failed")
            raise ValueError("first")
        ran.append("found")
        return "found"

    audit = _CapturingLogger()
    await _agent(
        flaky, sink=audit, capabilities=[_RetryOnce(), _Veto(allow=1)], tool_failure=False
    ).run("go", deps=_deps())

    expected = {
        "failed": ("flaky", '{"n": 0}', False, "ValueError: first", None),
        "found": ("flaky", '{"n": 0}', True, None, 5),
    }
    assert ran
    assert _records(audit) == [expected[outcome] for outcome in ran]


class _RefuseTheSecondAttempt(AbstractCapability[Any]):
    """Asks for a retry from ``before_tool_execute`` on its second call only.

    Unpinned, so it runs ahead of audit's ``before_tool_execute``.
    """

    def __init__(self) -> None:
        self._attempts = 0

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        self._attempts += 1
        if self._attempts == 2:
            raise ModelRetry("not again")
        return args


async def test_a_rerun_refused_ahead_of_audit_is_not_recorded() -> None:
    """A wrapper runs the tool again after a failure, and a capability ahead of
    audit refuses that second run with a ``ModelRetry``; the model's own retry
    then runs the tool.

    From 2.54 the rerun enters audit's wrapper again, and the refusal reaches
    it before audit's ``before_tool_execute`` has run for that entry. Only the
    reset on the way out of the first entry keeps the second from carrying
    over the first's "reached" and recording a call that never ran. Before
    2.54 every ``before`` hook runs once per call, ahead of the wrappers, so
    the refusal falls on the model's retry instead, and the tool runs twice
    inside the first call. The records follow the tool either way.
    """
    ran: list[str] = []

    def flaky(n: int) -> str:
        """Fails the first time."""
        if not ran:
            ran.append("failed")
            raise ValueError("first")
        ran.append("found")
        return "found"

    audit = _CapturingLogger()
    await _agent(
        flaky,
        sink=audit,
        capabilities=[_RetryOnce(), _RefuseTheSecondAttempt()],
        tool_failure=False,
    ).run("go", deps=_deps())

    assert ran == ["failed", "found"]
    assert _records(audit) == [
        ("flaky", '{"n": 0}', False, "ValueError: first", None),
        ("flaky", '{"n": 0}', True, None, 5),
    ]


async def test_a_rerun_sorted_after_audit_and_refused_ahead_of_it_mixes_two_runs() -> None:
    """A wrapper sorted after audit runs the tool again after the tool's own
    ``ModelRetry``, and a capability ahead of audit refuses that second run.

    From 2.54 audit's hooks saw neither outcome, the retry having been routed
    past them and the refusal arriving before audit's ``before_tool_execute``,
    so audit's wrapper records the refusal with the first run's arguments and
    a time spanning the wrapper's pause. The model's own retry then gets a
    record of its own. Before 2.54 every ``before`` hook runs once per call,
    ahead of the wrappers, so nothing refuses the rerun, and the record holds
    its outcome as for any rerun.
    """
    ran: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        ran.append(n)
        if len(ran) == 1:
            raise ModelRetry("kaboom")
        return "found again"

    audit = _CapturingLogger()
    agent = _agent(lookup, sink=audit, capabilities=[_RefuseTheSecondAttempt()])
    async with agent.iter("go", deps=_deps(), capabilities=[_OneThing("wrapper-reruns")]) as run:
        async for _ in run:
            pass

    if _HOOKS_INSIDE_THE_WRAPPER:
        refused = ("lookup", '{"n": 0}', False, "ModelRetry: not again", None)
        assert ran == [0, 0]
        assert _records(audit) == [refused, _FOUND_AGAIN]
    else:
        assert ran == [0, 5]
        assert _records(audit) == [_FOUND_AGAIN]
    assert audit.events[0].duration_ms >= 300


async def test_the_result_size_is_the_tools_own_result() -> None:
    def lookup(n: int) -> str:
        """Look a thing up."""
        return "found"

    audit = _CapturingLogger()
    result = await _agent(lookup, sink=audit, capabilities=[_RewriteResult()]).run(
        "go", deps=_deps()
    )

    assert _records(audit) == [("lookup", '{"n": 0}', True, None, 5)]
    assert [part.content for part in _tool_returns(result.all_messages())] == ["y" * 500]


def _sync_lookup(n: int) -> str:
    """Look a thing up, blocking."""
    time.sleep(0.05)
    return "found"


async def _async_lookup(n: int) -> str:
    """Look a thing up."""
    await asyncio.sleep(0.05)
    return "found"


@pytest.mark.parametrize("lookup", [_sync_lookup, _async_lookup], ids=["sync", "async"])
async def test_the_duration_is_the_tools_own(lookup: Callable[..., Any]) -> None:
    """``duration_ms`` spans the tool, not the 1.2 s of another capability's hooks."""
    audit = _CapturingLogger()
    await _agent(lookup, sink=audit, capabilities=[_SlowHooks()]).run("go", deps=_deps())

    assert _records(audit) == [(lookup.__name__, '{"n": 0}', True, None, 5)]
    assert 50 <= audit.events[0].duration_ms < 300


class _Overlap:
    """Holds two tool calls in flight together, then lets the first finish.

    The first call to arrive waits for the second, then finishes while the
    second is still running. That is the order that crosses two calls' state
    if any of it is shared: the first call's result hooks run after the second
    call's argument hooks.
    """

    def __init__(self) -> None:
        self._arrived = 0
        self._second = asyncio.Event()

    async def __call__(self) -> None:
        self._arrived += 1
        if self._arrived == 1:
            await asyncio.wait_for(self._second.wait(), timeout=5)
        else:
            self._second.set()
            await asyncio.sleep(0.05)


async def test_parallel_calls_in_one_run_keep_their_own_records() -> None:
    """Two calls in flight at once, each its own arguments, outcome and size."""
    overlap = _Overlap()

    async def first(n: int) -> str:
        """The first tool."""
        await overlap()
        return "a" * 10

    async def second(n: int) -> str:
        """The second tool."""
        await overlap()
        raise ValueError("second broke")

    audit = _CapturingLogger()
    model = _scripted(
        [
            ToolCallPart(tool_name="first", args={"n": 1}, tool_call_id="call"),
            ToolCallPart(tool_name="second", args={"n": 2}, tool_call_id="call-2"),
        ]
    )
    await _agent(first, second, sink=audit, model=model, capabilities=[_RewriteResult()]).run(
        "go", deps=_deps()
    )

    assert sorted(_records(audit)) == [
        ("first", '{"n": 1}', True, None, 10),
        ("second", '{"n": 2}', False, "ValueError: second broke", None),
    ]


async def test_concurrent_runs_of_one_agent_keep_their_own_records() -> None:
    """Two runs of one agent, in flight at once, with the same tool call id.

    ``TestModel`` gives every run the same ``tool_call_id``, so per-call state
    keyed on that id alone would cross the two over. The runs differ by their
    deps: one tool call succeeds and the other fails.
    """
    overlap = _Overlap()

    async def lookup(ctx: RunContext[AgentDeps], n: int) -> str:
        """Look a thing up for this client."""
        await overlap()
        if ctx.deps.ip_address == "10.0.0.2":
            raise ValueError("second client")
        return "x" * 3

    audit = _CapturingLogger()
    agent = _agent(lookup, sink=audit, capabilities=[_RewriteResult()])
    await asyncio.gather(
        agent.run("go", deps=_deps("10.0.0.1")),
        agent.run("go", deps=_deps("10.0.0.2")),
    )

    assert sorted((e.ip_address, e.success, e.error, e.result_size) for e in audit.events) == [
        ("10.0.0.1", True, None, 3),
        ("10.0.0.2", False, "ValueError: second client", None),
    ]


async def test_two_audit_capabilities_in_one_chain_each_record_the_call() -> None:
    """A project's own ``AuditCapability`` beside the one ``build_agent`` adds.

    Both are innermost and observe the same call through the same hooks, so
    each keeps its own state rather than one reading the other's.
    """

    def lookup(n: int) -> str:
        """Look a thing up."""
        return "found"

    own, built = _CapturingLogger(), _CapturingLogger()
    await _agent(lookup, sink=built, capabilities=[AuditCapability(own)]).run("go", deps=_deps())

    assert _records(own) == _records(built) == [("lookup", '{"n": 0}', True, None, 5)]


async def test_a_nested_run_sharing_the_capability_records_its_own_calls() -> None:
    """A tool that runs another agent composed with the same capability.

    The inner calls start while the outer one is still in flight, inside it,
    so each must keep its own state: the inner record is the inner tool's, an
    inner call a guardrail refuses has none, and the outer record still
    measures the outer tool's own result.
    """
    audit_log = _CapturingLogger()
    audit = AuditCapability(audit_log)

    def leaf(n: int) -> str:
        """The inner tool."""
        return "leaf"

    def blocked(n: int) -> str:
        """An inner tool the guardrail refuses."""
        return "never"

    inner = Agent(
        TestModel(call_tools=["leaf", "blocked"]),
        toolsets=[FunctionToolset([leaf, blocked])],
        capabilities=[_Veto(tools=frozenset({"blocked"})), audit],
    )

    async def delegate(n: int) -> str:
        """The outer tool."""
        await inner.run("go")
        return "delegated"

    outer = Agent(
        TestModel(call_tools=["delegate"]),
        toolsets=[FunctionToolset([delegate])],
        capabilities=[audit],
    )
    await outer.run("go")

    assert _records(audit_log) == [
        ("leaf", '{"n": 0}', True, None, 4),
        ("delegate", '{"n": 0}', True, None, 9),
    ]


async def test_a_nested_call_refused_ahead_of_audit_is_not_recorded() -> None:
    """A tool runs another agent composed with the same capability, whose first
    call a capability ahead of audit refuses with a ``ModelRetry``.

    From 2.54 the refusal reaches the inner call's audit wrapper, which starts
    in a context still holding the outer call's execution, before audit's
    ``before_tool_execute`` has run for the inner call. Only the identity
    check keeps the inner call from taking the outer one's "reached" and
    recording a call that never ran.
    """
    audit_log = _CapturingLogger()
    audit = AuditCapability(audit_log)
    ran: list[int] = []

    def leaf(n: int) -> str:
        """The inner tool."""
        ran.append(n)
        return "leaf"

    inner = Agent(
        TestModel(call_tools=["leaf"]),
        toolsets=[FunctionToolset([leaf])],
        capabilities=[_RetryFirstCall(), audit],
    )

    async def delegate(n: int) -> str:
        """The outer tool."""
        await inner.run("go")
        return "delegated"

    outer = Agent(
        TestModel(call_tools=["delegate"]),
        toolsets=[FunctionToolset([delegate])],
        capabilities=[audit],
    )
    await outer.run("go")

    assert ran == [0]
    assert _records(audit_log) == [
        ("leaf", '{"n": 0}', True, None, 4),
        ("delegate", '{"n": 0}', True, None, 9),
    ]


async def test_no_per_call_state_outlives_the_run() -> None:
    """Including on the paths where some of audit's hooks never run.

    A veto leaves the tool unrun, an approval deferral stops before any hook,
    and a tool's own ``ToolFailed`` skips the error and result hooks. Per-call
    state lives in the tool call's own context, so none survives the run.
    """

    def lookup(n: int) -> str:
        """Look a thing up."""
        raise ToolFailed("model copy")

    def drop(n: int) -> str:
        """Delete a thing."""
        return "dropped"

    audit = _CapturingLogger()
    await _agent(lookup, sink=audit, capabilities=[_Veto()]).run("go", deps=_deps())
    await _agent(lookup, sink=audit).run("go", deps=_deps())
    await _agent(
        drop,
        sink=audit,
        tool_guard=ToolGuardConfig(enabled=True, require_approval=frozenset({"drop"})),
    ).run("go", deps=_deps())
    gc.collect()

    assert [o for o in gc.get_objects() if type(o).__name__ == "_Execution"] == []
    assert len(audit.events) == 1


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
    assert _records(audit) == [("boom", "{}", False, "ValueError: kaboom", None)]


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


# What a ``ToolFailed`` from anything but the failure policy records. Each is
# the failure as its raiser chose to state it, so the record keeps its message,
# whatever it was raised from or while handling.


def _tool_failed_from_a_cause() -> str:
    raise ToolFailed("Order 42 does not exist.") from LookupError("no row with pk=42")


def _tool_failed_from_an_empty_timeout() -> str:
    # The shape of a spec tool's timeout: the limit is in the message, and the
    # cause is an ``asyncio`` timeout whose text is empty.
    raise ToolFailed("This call took longer than the 5s limit.") from TimeoutError()


def _tool_failed_while_handling() -> str:
    try:
        raise LookupError("no row with pk=42")
    except LookupError:
        raise ToolFailed("Order 42 does not exist.")  # noqa: B904 -- the implicit context is the case


def _tool_failed_from_none() -> str:
    try:
        raise LookupError("no row with pk=42")
    except LookupError:
        raise ToolFailed("Order 42 does not exist.") from None


def _another_exception_from_a_cause() -> str:
    raise ValueError("outer") from KeyError("inner")


def _calls_lookup_from_the_sandbox(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(parts=[ToolCallPart("run_code", {"code": "await lookup()"})])
    return ModelResponse(parts=[TextPart("done")])


async def _run_lookup_under_code_mode(
    lookup: Callable[[], str], *capabilities: Any
) -> tuple[list[str | None], str]:
    """Run ``lookup`` from inside a code-mode sandbox. Returns the audit's
    failure records for it, and the retry text the model was sent for the
    ``run_code`` call, which carries what the sandbox saw.

    Code mode calls the sandbox's tools through a nested tool manager that
    inherits the agent's capabilities but leaves a ``ToolFailed`` unconverted,
    so a tool's own ``ToolFailed`` reaches every hook as raised.
    """
    audit = _CapturingLogger()
    agent = Agent(
        FunctionModel(_calls_lookup_from_the_sandbox),
        toolsets=[FunctionToolset([Tool(lookup, name="lookup")])],
        capabilities=[CodeMode(), AuditCapability(audit), *capabilities],
    )
    result = await agent.run("lookup")
    retries = [
        str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart) and part.tool_name == "run_code"
    ]
    failures = [e.error for e in audit.events if not e.success and e.tool_name == "lookup"]
    return failures, "\n".join(retries)


async def _run_lookup_directly(lookup: Callable[[], str]) -> list[str | None]:
    """Run ``lookup`` as an ordinary tool call, with the failure policy on as a
    deployment has it. Returns the audit's failure records for it."""
    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["lookup"]),
        toolsets=[FunctionToolset([Tool(lookup, name="lookup")])],
        capabilities=[AuditCapability(audit), ToolFailurePolicy()],
    )
    await agent.run("lookup")
    return [e.error for e in audit.events if not e.success]


@pytest.mark.parametrize(
    ("raise_it", "message"),
    [
        (_tool_failed_from_a_cause, "Order 42 does not exist."),
        (_tool_failed_from_an_empty_timeout, "This call took longer than the 5s limit."),
        (_tool_failed_while_handling, "Order 42 does not exist."),
        (_tool_failed_from_none, "Order 42 does not exist."),
    ],
    ids=["from-a-cause", "empty-timeout", "implicit-context", "from-none"],
)
@pytest.mark.parametrize("route", ["direct", "code-mode"])
async def test_a_tools_own_tool_failed_keeps_its_own_message(
    raise_it: Callable[[], str], message: str, route: str
) -> None:
    """Never its cause, and never its context.

    The cause can say less than the message (the empty timeout), and the
    context is whatever the tool happened to be handling. On an ordinary call
    pydantic-ai converts the tool's ``ToolFailed`` into a ``ToolFailedError``
    with the same message before any hook sees it; a code-mode sandbox's
    nested tool manager leaves it as raised.
    """
    if route == "direct":
        assert await _run_lookup_directly(raise_it) == [f"ToolFailedError: {message}"]
    else:
        failures, _ = await _run_lookup_under_code_mode(raise_it, ToolFailurePolicy())
        assert failures == [f"ToolFailed: {message}"]


@pytest.mark.parametrize("route", ["direct", "code-mode"])
async def test_any_other_exception_records_itself_not_its_cause(route: str) -> None:
    if route == "direct":
        failures = await _run_lookup_directly(_another_exception_from_a_cause)
    else:
        failures, _ = await _run_lookup_under_code_mode(
            _another_exception_from_a_cause, ToolFailurePolicy()
        )

    assert failures == ["ValueError: outer"]


async def test_under_code_mode_a_tools_model_retry_is_recorded_as_raised() -> None:
    """The sandbox's nested tool manager does not turn it into a
    ``ToolRetryError``, as an ordinary call does."""

    def lookup() -> str:
        """Look up order 42."""
        raise ModelRetry("please retry")

    failures, _ = await _run_lookup_under_code_mode(lookup, ToolFailurePolicy())

    assert failures == ["ModelRetry: please retry"]


async def test_under_code_mode_the_policys_translation_records_the_exception() -> None:
    """The record names the exception, and the sandbox sees the policy's raise
    as a plain ``Exception`` carrying its message, as it sees any
    ``ToolFailed``: the private subclass the policy raises is not visible to
    the model's script."""

    def lookup() -> str:
        """Look up order 42."""
        raise RuntimeError("hunter2")

    failures, sandbox_saw = await _run_lookup_under_code_mode(lookup, ToolFailurePolicy())

    assert failures == ["RuntimeError: hunter2"]
    assert "Exception: The lookup tool failed and returned no result." in sandbox_saw
    assert "PolicyToolFailed" not in sandbox_saw
    assert "hunter2" not in sandbox_saw


async def test_a_spec_tools_timeout_keeps_its_message() -> None:
    """The real producer of the empty-timeout shape above.

    ``SpecToolset`` raises a ``ToolFailed`` naming its limit from the
    ``asyncio`` timeout, whose own text is empty, so a record describing the
    cause would read ``TimeoutError: `` and lose the only detail there is.
    """

    def slow(user: Any) -> dict[str, Any]:
        """Take longer than the limit."""
        time.sleep(0.2)
        return {}

    audit = _CapturingLogger()
    spec = ServiceSpec(service=slow, atomic=False, permission_classes=[AllowAny])
    agent = _agent(
        sink=audit,
        model=TestModel(call_tools=["slow"]),
        capabilities=[SpecCapability({"slow": spec}, dispatch_timeout=0.01)],
    )
    await agent.run("go", deps=_deps())

    [event] = audit.events
    assert event.success is False
    assert event.error is not None
    assert event.error.startswith("ToolFailedError: This call took longer than the 0.01s limit")


class _Hook(AbstractCapability[Any]):
    """A before- or after-hook that rewrites what passes through it, or rejects
    the call outright."""

    def __init__(self, *, stage: str, reject: bool) -> None:
        self._stage = stage
        self._reject = reject

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if self._stage != "before":
            return args
        if self._reject:
            raise PermissionError("rejected before execution")
        return {**args, "secret": "[redacted]"}

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        if self._stage != "after":
            return result
        if self._reject:
            raise PermissionError("rejected after execution")
        return "x" * 1000


# Each record as (success, error, result_size, whether the before-hook's
# redaction reached arguments_repr). The tool returns "ok", so its own result
# is 2 long and the after-hook's rewrite is 1000.
_OK = (True, None, 2, False)


@pytest.mark.parametrize(
    ("stage", "reject", "recorded"),
    [
        ("before", False, [(True, None, 2, True)]),
        ("before", True, []),
        ("after", False, [_OK]),
        ("after", True, [_OK]),
    ],
    ids=["before-rewrite", "before-reject", "after-rewrite", "after-reject"],
)
async def test_a_before_or_after_hook_reaches_the_record_only_through_the_tool(
    stage: str,
    reject: bool,
    recorded: list[tuple[bool, str | None, int | None, bool]],
) -> None:
    """The same record on every pydantic-ai, whichever side of the wrapper the
    installed release runs these hooks.

    A before-hook's rewrite is in the arguments, because it is what the tool
    received. A before-hook's rejection leaves no record, because the tool
    never ran. The size is the tool's own result, not the after-hook's
    rewrite, and an after-hook's rejection comes after a tool that succeeded.
    """

    def echo(secret: str) -> str:
        """Echo."""
        return "ok"

    audit = _CapturingLogger()
    agent = Agent(
        TestModel(call_tools=["echo"]),
        toolsets=[FunctionToolset([echo])],
        capabilities=[AuditCapability(audit), _Hook(stage=stage, reject=reject)],
    )
    if reject:
        with pytest.raises(PermissionError):
            await agent.run("echo")
    else:
        await agent.run("echo")

    records = [
        (e.success, e.error, e.result_size, "[redacted]" in e.arguments_repr) for e in audit.events
    ]
    assert records == recorded
