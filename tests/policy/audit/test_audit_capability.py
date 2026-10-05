from __future__ import annotations

import logging
import pickle
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai import __version__ as pydantic_ai_version
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness import CodeMode

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.audit.audit_capability import AuditCapability
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.failure.tool_failure_policy import ToolFailurePolicy


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


_CALL = ToolCallPart(tool_name="boom", args={})
_TOOL_DEF = ToolDefinition(name="boom", parameters_json_schema={"type": "object"})


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
            call=_CALL,
            tool_def=_TOOL_DEF,
            args={},
            handler=handler,
        )
    [event] = audit.events
    return event, raised.value


async def _policy_translation(error: Exception) -> BaseException:
    """What ``ToolFailurePolicy`` raises in place of ``error``, taken from the
    policy itself rather than built here, so these tests follow its real type."""
    with pytest.raises(ToolFailed) as raised:
        await ToolFailurePolicy().on_tool_execute_error(
            SimpleNamespace(deps=None),
            call=_CALL,
            tool_def=_TOOL_DEF,
            args={},
            error=error,
        )
    return raised.value


async def test_the_policys_translation_records_the_exception_it_was_raised_from() -> None:
    """From pydantic-ai 2.54 the policy's ``on_tool_execute_error`` runs inside
    ``wrap_tool_execute``, so the wrapper is handed its *translation*. The
    operator's record names the exception the translation was raised from, not
    the model-facing text."""
    translated = await _policy_translation(RuntimeError("kaboom"))

    async def handler(args: dict[str, Any]) -> Any:
        raise translated

    event, raised = await _record_failure(handler)

    assert event.success is False
    assert event.error == "RuntimeError: kaboom"
    # Unwrapping is for the record only: the model still gets the failed result.
    assert raised is translated


def _tool_failed_from_a_cause() -> None:
    raise ToolFailed("Order 42 does not exist.") from LookupError("no row with pk=42")


def _tool_failed_from_an_empty_timeout() -> None:
    # A spec tool's timeout has this shape: the limit is in the message, and the
    # cause is an ``asyncio`` timeout whose text is empty.
    raise ToolFailed("This call took longer than the 5s limit.") from TimeoutError()


def _tool_failed_while_handling() -> None:
    try:
        raise LookupError("no row with pk=42")
    except LookupError:
        raise ToolFailed("Order 42 does not exist.")  # noqa: B904 -- the implicit context is the case


def _tool_failed_from_none() -> None:
    try:
        raise LookupError("no row with pk=42")
    except LookupError:
        raise ToolFailed("Order 42 does not exist.") from None


def _another_exception_from_a_cause() -> None:
    raise ValueError("outer") from KeyError("inner")


@pytest.mark.parametrize(
    ("raise_it", "recorded"),
    [
        (_tool_failed_from_a_cause, "ToolFailed: Order 42 does not exist."),
        (
            _tool_failed_from_an_empty_timeout,
            "ToolFailed: This call took longer than the 5s limit.",
        ),
        (_tool_failed_while_handling, "ToolFailed: Order 42 does not exist."),
        (_tool_failed_from_none, "ToolFailed: Order 42 does not exist."),
        (_another_exception_from_a_cause, "ValueError: outer"),
    ],
    ids=[
        "tool-failed-from-a-cause",
        "empty-timeout",
        "implicit-context",
        "from-none",
        "not-a-tool-failed",
    ],
)
async def test_anything_but_the_policys_translation_records_itself(
    raise_it: Callable[[], None], recorded: str
) -> None:
    """Every other ``ToolFailed`` is the failure as its raiser chose to state it,
    and its cause can say less than its message (the empty timeout). Any other
    exception is the failure itself. Each is recorded as raised."""

    async def handler(args: dict[str, Any]) -> Any:
        raise_it()

    event, _ = await _record_failure(handler)

    assert event.error == recorded


@pytest.mark.parametrize("strip", ["pickled-copy", "from-none"])
async def test_a_translation_without_a_cause_records_itself(strip: str) -> None:
    """A translation whose cause is gone, as it is from a pickled copy, is
    recorded as itself rather than as ``NoneType: None``.

    Raised while another exception is being handled, so ``__context__`` is set:
    only ``__cause__`` is the exception a translation was raised *from*, and a
    record reading the context would name the wrong failure here.
    """
    translated = await _policy_translation(RuntimeError("kaboom"))

    async def handler(args: dict[str, Any]) -> Any:
        try:
            raise LookupError("no row with pk=42")
        except LookupError:
            if strip == "pickled-copy":
                raise pickle.loads(pickle.dumps(translated))  # noqa: B904 -- the implicit context is the case
            raise translated from None

    event, raised = await _record_failure(handler)

    assert raised.__context__ is not None
    assert event.error == f"{type(translated).__name__}: {translated}"


async def test_a_tool_raising_tool_failed_records_its_own_message() -> None:
    """A tool's own ``raise ToolFailed(...) from error`` keeps the message the
    tool chose as its outcome.

    On an ordinary call pydantic-ai turns a tool's ``ToolFailed`` into a
    ``ToolFailedError`` (raised from it) inside the execution step, before any
    wrapper sees it, under either hook order.
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
    so the audit wrapper is handed a tool's own ``ToolFailed`` as raised.
    """
    audit = _CapturingLogger()
    agent = Agent(
        FunctionModel(_calls_lookup_from_the_sandbox),
        toolsets=[FunctionToolset([lookup])],
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


async def test_under_code_mode_a_tools_own_tool_failed_records_its_own_message() -> None:
    def lookup() -> str:
        """Look up order 42."""
        raise ToolFailed("Order 42 does not exist.") from LookupError("SELECT ... WHERE id = 42")

    failures, _ = await _run_lookup_under_code_mode(lookup)

    assert failures == ["ToolFailed: Order 42 does not exist."]


async def test_under_code_mode_the_policys_translation_records_the_exception() -> None:
    """The record names the exception, and the sandbox sees the policy's raise
    as a plain ``Exception`` carrying its message, as it sees any
    ``ToolFailed``: the private subclass marking it is not visible to the
    model's script."""

    def lookup() -> str:
        """Look up order 42."""
        raise RuntimeError("hunter2")

    failures, sandbox_saw = await _run_lookup_under_code_mode(lookup, ToolFailurePolicy())

    assert failures == ["RuntimeError: hunter2"]
    assert "Exception: The lookup tool failed and returned no result." in sandbox_saw
    assert "PolicyToolFailed" not in sandbox_saw
    assert "hunter2" not in sandbox_saw


# From pydantic-ai 2.54 ``before_tool_execute`` and ``after_tool_execute`` run
# inside ``wrap_tool_execute``; before it, outside. Both orders are in the test
# matrix (the locked release and the lowest declared one), so the test below
# states what each one records.
_HOOKS_INSIDE_THE_WRAPPER = tuple(int(part) for part in pydantic_ai_version.split(".")[:2]) >= (
    2,
    54,
)


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
# redaction reached arguments_repr). The tool returns "ok", so an untouched
# result is 2 long and the after-hook's rewrite is 1000.
_OK = (True, None, 2, False)


@pytest.mark.parametrize(
    ("stage", "reject", "inside", "outside"),
    [
        ("before", False, [_OK], [(True, None, 2, True)]),
        (
            "before",
            True,
            [(False, "PermissionError: rejected before execution", None, False)],
            [],
        ),
        ("after", False, [(True, None, 1000, False)], [_OK]),
        (
            "after",
            True,
            [(False, "PermissionError: rejected after execution", None, False)],
            [_OK],
        ),
    ],
    ids=["before-rewrite", "before-reject", "after-rewrite", "after-reject"],
)
async def test_what_a_before_or_after_hook_does_reaches_the_record_by_hook_order(
    stage: str,
    reject: bool,
    inside: list[tuple[bool, str | None, int | None, bool]],
    outside: list[tuple[bool, str | None, int | None, bool]],
) -> None:
    """The record carries what the wrapper was handed and what it returned, and
    sees what raises inside it.

    From 2.54 both hooks run inside the wrapper: the record carries the
    validated arguments rather than a before-hook's rewrite, a before-hook's
    rejection is a failed record, the result size is the after-hook's output,
    and an after-hook's rejection is a failed record. Before 2.54 both ran
    outside it: the record carried the rewritten arguments, a before-hook's
    rejection left no record, the size was the tool's own result, and an
    after-hook's rejection followed a record of success.
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
    assert records == (inside if _HOOKS_INSIDE_THE_WRAPPER else outside)
