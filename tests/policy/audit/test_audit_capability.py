from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections.abc import Callable, Sequence
from importlib.metadata import version
from typing import Any

import pytest
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import SkipToolExecution, ToolFailed
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.audit.audit_capability import AuditCapability
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry

# Every scenario below is a real agent run: a real ``Agent``, real capabilities
# and pydantic-ai's own hook dispatch. Which hook encloses which is the
# installed pydantic-ai's decision (2.54 moved every ``before`` / ``after`` /
# ``on_error`` hook inside ``wrap_tool_execute``), so a hand-driven hook would
# only restate this file's assumption about it. The suite runs on both orders,
# and every record asserted here is the same on both.


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
        await agent.run("go", deps=_deps())
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


async def test_audit_composed_ahead_of_an_innermost_veto_sees_it_from_2_54() -> None:
    """Why ``build_agent`` appends audit after ``config.capabilities``.

    List order breaks ties within the innermost tier. Composed by hand ahead of
    an innermost guardrail, audit's ``before_tool_execute`` runs before the
    veto, and from pydantic-ai 2.54, where the wrapper encloses that hook, the
    vetoed call is recorded as a failure. Earlier releases run the veto outside
    the wrapper, and nothing is recorded.
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

    wrapper_encloses_hooks = tuple(int(p) for p in version("pydantic-ai-slim").split(".")[:2]) >= (
        2,
        54,
    )
    assert _records(audit) == (
        [("lookup", '{"n": 0}', False, "SkipToolExecution: ", None)]
        if wrapper_encloses_hooks
        else []
    )


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


async def test_the_arguments_are_the_ones_the_tool_received() -> None:
    received: list[int] = []

    def lookup(n: int) -> str:
        """Look a thing up."""
        received.append(n)
        return "found"

    audit = _CapturingLogger()
    await _agent(lookup, sink=audit, capabilities=[_RewriteArgs()]).run("go", deps=_deps())

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
