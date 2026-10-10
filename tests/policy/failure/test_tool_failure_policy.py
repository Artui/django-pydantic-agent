from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from importlib.metadata import version
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import RequestFactory
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ToolFailed, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness.step_persistence import StepPersistence

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.contrib.store.default_step_store import DefaultStepStore
from django_pydantic_agent.policy.failure.tool_failure_policy import ToolFailurePolicy
from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.failure.utils import PolicyToolFailed
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry

# pydantic-ai 2.54 moved every ``before`` / ``on_error`` / ``after`` hook inside
# the ``wrap_tool_execute`` chain, which changes what a wrapper sees.
_HOOKS_INSIDE_THE_WRAPPER = tuple(
    int(part) for part in version("pydantic-ai-slim").split(".")[:2]
) >= (2, 54)


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
    result = await _agent().run("go", deps=AgentDeps(user=None))

    returns = _tool_returns(result)
    assert len(returns) == 1
    assert returns[0].outcome == "failed"
    assert "boom" in returns[0].content


async def test_the_default_message_does_not_carry_the_exception_text() -> None:
    # An exception message is written for an operator. Anything handed to the
    # model is also handed to whatever renders the transcript.
    result = await _agent().run("go", deps=AgentDeps(user=None))

    assert "hunter2" not in _tool_returns(result)[0].content


async def test_include_detail_opts_into_the_exception_text() -> None:
    result = await _agent(tool_failure=ToolFailureConfig(include_detail=True)).run(
        "go", deps=AgentDeps(user=None)
    )

    content = _tool_returns(result)[0].content
    assert "RuntimeError" in content
    assert "hunter2" in content


async def test_disabled_restores_the_failing_run() -> None:
    with pytest.raises(RuntimeError, match="hunter2"):
        await _agent(tool_failure=ToolFailureConfig(enabled=False)).run(
            "go", deps=AgentDeps(user=None)
        )


async def test_the_operator_copy_is_never_redacted() -> None:
    """Audit still records the real failure against the tool that caused it.

    Audit's error hook runs before every other one and the policy's after every
    other one, so the exception audit keeps is the tool's, never the
    policy's redacted copy. The default ``include_detail`` off is what makes
    the difference visible: the copy carries none of the original text.
    """
    audit = _RecordingAudit()

    await _agent(audit=audit).run("go", deps=AgentDeps(user=None))

    assert [(e.tool_name, e.success, e.error) for e in audit.events] == [
        ("boom", False, "RuntimeError: credentials=hunter2")
    ]


class _ErrorHook(AbstractCapability[Any]):
    """An ``on_tool_execute_error`` of a project's own, recording what it is handed.

    ``answer`` is what it does with it: ``None`` propagates the error, an
    exception is raised in its place, and anything else recovers the call.
    """

    def __init__(self, answer: Any = None) -> None:
        self.seen: list[Exception] = []
        self._answer = answer

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
        if self._answer is None:
            raise error
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _composed(
    *capabilities: AbstractCapability[Any],
    error: Exception,
    retries: int = 1,
    audit: Any = None,
) -> Agent[AgentDeps, Any]:
    """An agent whose one tool raises ``error``, with ``capabilities`` in
    ``config.capabilities`` the way a project adds its own."""
    reg = ToolRegistry()
    calls: list[int] = []

    @tool(reg)
    def boom(target: str) -> str:
        """Raise on purpose, the first time."""
        calls.append(1)
        if len(calls) == 1:
            raise error
        return "ok"

    return build_agent(
        reg,
        AgentConfig(
            model=TestModel(call_tools=["boom"]),
            capabilities=list(capabilities),
            retries=retries,
            audit_logger=audit,
        ),
    )


def test_the_policy_declares_outermost_ordering() -> None:
    """Outermost, so its error hook is the last of every capability's to run."""
    ordering = ToolFailurePolicy().get_ordering()
    assert ordering is not None
    assert ordering.position == "outermost"


async def test_every_other_error_hook_sees_the_tools_exception() -> None:
    """The policy converts last, so a capability composed beside it is handed
    the exception the tool raised rather than the policy's redacted copy, and
    the model still gets the policy's failed result."""
    hook = _ErrorHook()
    error = RuntimeError("credentials=hunter2")

    result = await _composed(hook, error=error).run("go", deps=AgentDeps(user=None))

    assert hook.seen == [error]
    returns = _tool_returns(result)
    assert [(r.outcome, r.content) for r in returns] == [
        (
            "failed",
            "The boom tool failed and returned no result. "
            "The failure has been recorded; do not retry the same call.",
        )
    ]


class _OutermostErrorHook(_ErrorHook):
    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")


async def test_an_outermost_error_hook_still_sees_the_tools_exception() -> None:
    """Pinning the policy outermost is not enough on its own: list order breaks
    ties within the tier, so ``build_agent`` also places it ahead of
    ``config.capabilities``, and it still converts after this one."""
    hook = _OutermostErrorHook()
    error = RuntimeError("credentials=hunter2")

    result = await _composed(hook, error=error).run("go", deps=AgentDeps(user=None))

    assert hook.seen == [error]
    assert [r.outcome for r in _tool_returns(result)] == ["failed"]


async def test_the_policy_raises_its_own_tool_failed_subclass() -> None:
    """What the policy raises is ``PolicyToolFailed``, from the tool's exception.

    Seen from an error hook composed by hand ahead of the policy in the
    outermost tier, which is the one place whose error hook runs after the
    policy's, on every release. The subclass is what a trace names, and it
    never compares equal to a plain ``ToolFailed`` with the same message.
    """
    hook = _OutermostErrorHook()
    error = RuntimeError("credentials=hunter2")

    def boom(target: str) -> str:
        """Raise on purpose."""
        raise error

    agent = Agent(
        TestModel(call_tools=["boom"]),
        tools=[boom],
        capabilities=[hook, ToolFailurePolicy()],
    )
    await agent.run("go")

    [raised] = hook.seen
    assert type(raised) is PolicyToolFailed
    assert raised.__cause__ is error
    assert raised != ToolFailed(raised.message)


class _Wrapper(AbstractCapability[Any]):
    """Keeps the exception that reaches its ``wrap_tool_execute``."""

    def __init__(self) -> None:
        self.seen: list[Exception] = []

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
        except Exception as error:
            self.seen.append(error)
            raise


async def test_a_wrapper_sees_the_policys_subclass_only_from_2_54() -> None:
    """Which exception a failed tool span names.

    pydantic-ai's instrumentation records a failed tool from a wrapper. From
    2.54 every wrapper encloses every error hook, so the exception reaching one
    is the policy's ``PolicyToolFailed``. Before 2.54 the error hooks run after
    the wrappers have returned, so a wrapper sees the tool's own exception and
    the policy's type never reaches it: the assertion is split there because
    the two orders genuinely differ, not to skip one.
    """
    wrapper = _Wrapper()
    error = RuntimeError("credentials=hunter2")

    await _composed(wrapper, error=error).run("go", deps=AgentDeps(user=None))

    [seen] = wrapper.seen
    if _HOOKS_INSIDE_THE_WRAPPER:
        assert type(seen) is PolicyToolFailed
        assert seen.__cause__ is error
    else:
        assert seen is error


@pytest.mark.django_db(transaction=True)
async def test_a_step_store_logs_the_tools_exception_not_the_policys() -> None:
    """harness's ``StepPersistence`` logs ``tool_call_failed`` from its error hook.

    That event is the operator's record of the step, so it must name the tool's
    exception rather than text written for the model.
    """
    request = RequestFactory().post("/")
    request.user = SimpleNamespace(is_authenticated=True, pk="7")  # type: ignore[attr-defined]
    store = DefaultStepStore(request)

    await _composed(
        StepPersistence(store=store, run_id="run-1"), error=RuntimeError("credentials=hunter2")
    ).run("go", deps=AgentDeps(user=None))

    failed = [e for e in await store.list_events(run_id="run-1") if e.kind == "tool_call_failed"]
    assert [(e.tool_name, e.error) for e in failed] == [
        ("boom", "RuntimeError('credentials=hunter2')")
    ]


async def test_a_recovering_capability_pre_empts_the_conversion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A capability that recovers answers first, so the policy never converts."""
    hook = _ErrorHook(answer="recovered")
    error = RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"):
        result = await _composed(hook, error=error).run("go", deps=AgentDeps(user=None))

    assert hook.seen == [error]
    assert [r.content for r in _tool_returns(result)] == ["recovered"]
    assert not [r for r in caplog.records if r.name == "django_pydantic_agent.failure"]


async def test_another_capabilitys_retry_is_not_converted() -> None:
    """``ModelRetry`` from an error hook is pydantic-ai's documented way to ask
    the model to redo the call. Converting it would end the retry it asked for.

    Holds the ``ModelRetry`` member of the policy's pass-through set.
    """
    hook = _ErrorHook(answer=ModelRetry("try another target"))

    result = await _composed(hook, error=RuntimeError("boom")).run("go", deps=AgentDeps(user=None))

    retries = [
        part
        for message in result.all_messages()
        for part in message.parts
        if type(part).__name__ == "RetryPromptPart"
    ]
    assert [part.content for part in retries] == ["try another target"]
    assert [r.content for r in _tool_returns(result)] == ["ok"]


async def test_another_capabilitys_retry_spends_the_tools_retry_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Passed through, it spends the tool's retry budget, and with none left
    the run ends.

    pydantic-ai turns a ``ModelRetry`` an error hook raises into a retry after
    every error hook has run, and raises ``UnexpectedModelBehavior`` there once
    the budget is spent, so the policy is never handed it and logs nothing.
    That is unlike a tool's own ``ModelRetry``, below. Audit has recorded the
    tool's exception.
    """
    hook = _ErrorHook(answer=ModelRetry("try another target"))
    audit = _RecordingAudit()

    with (
        caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"),
        pytest.raises(UnexpectedModelBehavior, match="exceeded max retries count of 0"),
    ):
        await _composed(hook, error=RuntimeError("boom"), retries=0, audit=audit).run(
            "go", deps=AgentDeps(user=None)
        )

    assert not [r for r in caplog.records if r.name == "django_pydantic_agent.failure"]
    assert [(e.success, e.error) for e in audit.events] == [(False, "RuntimeError: boom")]


async def test_a_tools_own_retry_with_no_budget_left_is_converted_into_a_failed_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With retries left a tool's own ``ModelRetry`` never reaches an error hook.

    Once the budget is spent, pydantic-ai raises ``UnexpectedModelBehavior``
    in its place from inside the call, which does reach the error hooks: audit
    records it, and the policy converts it into a failed result and logs it,
    where without the policy it would end the run.
    """
    reg = ToolRegistry()

    @tool(reg)
    def boom(target: str) -> str:
        """Always asks again."""
        raise ModelRetry("try another target")

    audit = _RecordingAudit()
    agent = build_agent(
        reg, AgentConfig(model=TestModel(call_tools=["boom"]), retries=0, audit_logger=audit)
    )
    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"):
        result = await agent.run("go", deps=AgentDeps(user=None))

    assert [(r.outcome, r.content) for r in _tool_returns(result)] == [
        (
            "failed",
            "The boom tool failed and returned no result. "
            "The failure has been recorded; do not retry the same call.",
        )
    ]
    assert [
        r.getMessage() for r in caplog.records if r.name == "django_pydantic_agent.failure"
    ] == ["django-pydantic-agent: tool 'boom' failed; the run continues with a failed result"]
    [event] = audit.events
    assert event.success is False
    assert event.error.startswith(
        "UnexpectedModelBehavior: Tool 'boom' exceeded max retries count of 0."
    )


async def test_a_tool_keeps_executing_after_its_retry_budget_is_spent() -> None:
    """The cost of converting a spent budget: nothing stops the model calling the
    tool again, so its side effects repeat. The model here calls it three times
    and then answers; with the policy off the first spent budget would end the run.
    """
    executions: list[str] = []
    reg = ToolRegistry()

    @tool(reg)
    def boom(target: str) -> str:
        """Always asks again."""
        executions.append(target)
        raise ModelRetry("try another target")

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(executions) < 3:
            return ModelResponse(parts=[ToolCallPart("boom", {"target": "x"})])
        return ModelResponse(parts=[TextPart("giving up")])

    agent = build_agent(reg, AgentConfig(model=FunctionModel(model_fn), retries=0))

    result = await agent.run("go", deps=AgentDeps(user=None))

    assert len(executions) == 3
    assert [r.outcome for r in _tool_returns(result)] == ["failed", "failed", "failed"]


async def test_reraising_unexpected_model_behavior_ends_the_run_on_a_spent_retry_budget() -> None:
    """The opt-out the docs describe for a tool's own ``ModelRetry``.

    The default converts it into a failed result (the test above). Naming
    ``UnexpectedModelBehavior`` in ``reraise`` hands it back to pydantic-ai,
    which ends the run, as it does without the policy. Its ``__cause__`` is the
    tool's ``ModelRetry``, which the next test shows a sub-agent's exhausted
    budget shares.
    """
    from django.core.exceptions import PermissionDenied

    reg = ToolRegistry()

    @tool(reg)
    def boom(target: str) -> str:
        """Always asks again."""
        raise ModelRetry("try another target")

    agent = build_agent(
        reg,
        AgentConfig(
            model=TestModel(call_tools=["boom"]),
            retries=0,
            tool_failure=ToolFailureConfig(reraise=(UnexpectedModelBehavior, PermissionDenied)),
        ),
    )

    with pytest.raises(UnexpectedModelBehavior, match="exceeded max retries count of 0") as info:
        await agent.run("go", deps=AgentDeps(user=None))

    assert isinstance(info.value.__cause__, ModelRetry)


async def test_reraising_unexpected_model_behavior_also_ends_the_run_for_a_sub_agents_budget() -> (
    None
):
    """The same entry catches every ``UnexpectedModelBehavior``, not only the
    one pydantic-ai raises for the tool's own spent budget: a tool that lets a
    sub-agent's exhausted budget propagate raises the same type with the same
    ``ModelRetry`` as its ``__cause__``, so the two cannot be told apart.
    """
    sub_registry = ToolRegistry()

    @tool(sub_registry)
    def inner(target: str) -> str:
        """Always asks again."""
        raise ModelRetry("try another target")

    sub_agent = build_agent(
        sub_registry,
        AgentConfig(
            model=TestModel(call_tools=["inner"]),
            retries=0,
            tool_failure=ToolFailureConfig(enabled=False),
        ),
    )
    reg = ToolRegistry()

    @tool(reg)
    async def delegate(target: str) -> str:
        """Delegates to a sub-agent and lets its failure propagate."""
        await sub_agent.run("go", deps=AgentDeps(user=None))
        return "unreachable"

    def agent_with(reraise: tuple[type[BaseException], ...] | None) -> Agent[AgentDeps, str]:
        return build_agent(
            reg,
            AgentConfig(
                model=TestModel(call_tools=["delegate"]),
                tool_failure=ToolFailureConfig(reraise=reraise),
            ),
        )

    converted = await agent_with(None).run("go", deps=AgentDeps(user=None))
    assert [r.outcome for r in _tool_returns(converted)] == ["failed"]

    with pytest.raises(UnexpectedModelBehavior, match="Tool 'inner'") as info:
        await agent_with((UnexpectedModelBehavior,)).run("go", deps=AgentDeps(user=None))

    assert isinstance(info.value.__cause__, ModelRetry)


async def test_another_capabilitys_failed_result_is_not_converted() -> None:
    """A ``ToolFailed`` from an error hook is that capability's own answer for
    the model, and the policy's message must not replace it.

    Holds the ``ToolFailed`` member of the policy's pass-through set.
    """
    hook = _ErrorHook(answer=ToolFailed("The upstream is down for maintenance."))

    result = await _composed(hook, error=RuntimeError("boom")).run("go", deps=AgentDeps(user=None))

    assert [(r.outcome, r.content) for r in _tool_returns(result)] == [
        ("failed", "The upstream is down for maintenance.")
    ]


async def test_a_denial_passes_every_error_hook_untouched() -> None:
    """Running last does not change what the policy does with a refusal: it
    still ends the run, and a capability composed beside it sees it first."""
    from django.core.exceptions import PermissionDenied

    hook = _ErrorHook()
    error = PermissionDenied("not yours")

    with pytest.raises(PermissionDenied) as raised:
        await _composed(hook, error=error).run("go", deps=AgentDeps(user=None))

    assert raised.value is error
    assert hook.seen == [error]


@pytest.mark.parametrize(
    "answer",
    [ModelRetry("try another target"), ToolFailed("The upstream is down."), "recovered"],
    ids=["retry", "failed-result", "recovered"],
)
async def test_the_failure_logger_hears_nothing_the_policy_did_not_convert(
    answer: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Another capability answered for the call, so the policy converted nothing
    and logged nothing."""
    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"):
        await _composed(_ErrorHook(answer=answer), error=RuntimeError("boom")).run(
            "go", deps=AgentDeps(user=None)
        )

    assert not [r for r in caplog.records if r.name == "django_pydantic_agent.failure"]


async def test_a_refusal_passing_through_is_not_logged_as_a_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from django.core.exceptions import PermissionDenied

    with (
        caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"),
        pytest.raises(PermissionDenied),
    ):
        await _composed(error=PermissionDenied("not yours")).run("go", deps=AgentDeps(user=None))

    assert not [r for r in caplog.records if r.name == "django_pydantic_agent.failure"]


async def test_the_failure_is_logged_with_its_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="django_pydantic_agent.failure"):
        await _agent().run("go", deps=AgentDeps(user=None))

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

    await agent.run("go", deps=AgentDeps(user=None))

    assert len(calls) == 2


def test_the_policy_can_be_built_without_a_config() -> None:
    # Composed directly rather than through ``build_agent``: the default record
    # is the same either way.
    assert isinstance(ToolFailurePolicy(), ToolFailurePolicy)


class TestAuthorizationIsNotAToolFailure:
    """A denial is the one failure the policy must not absorb.

    Converting it to a ``ToolFailed`` leaves the run alive with the model free to
    call the same tool on the next row, and a failed result is distinguishable
    from a "not found" one — so a denied-vs-missing sweep turns into an existence
    oracle over rows the acting user cannot read. ``ToolFailed`` spends no retry
    budget, and ``build_agent`` sets no ``UsageLimits``, so nothing bounds it.
    """

    def _denying_agent(
        self, error: BaseException, *, tool_failure: ToolFailureConfig | None = None
    ) -> Agent[AgentDeps, Any]:
        reg = ToolRegistry()

        @tool(reg)
        def get_invoice(invoice_id: int) -> str:
            """Read an invoice."""
            raise error

        return build_agent(
            reg,
            AgentConfig(
                model=TestModel(call_tools=["get_invoice"]),
                tool_failure=tool_failure if tool_failure is not None else ToolFailureConfig(),
            ),
        )

    async def test_a_drf_denial_aborts_the_run(self) -> None:
        from rest_framework.exceptions import PermissionDenied

        agent = self._denying_agent(PermissionDenied("not yours"))

        with pytest.raises(PermissionDenied):
            await agent.run("go", deps=AgentDeps(user=None))

    async def test_a_django_denial_aborts_the_run(self) -> None:
        from django.core.exceptions import PermissionDenied

        agent = self._denying_agent(PermissionDenied("not yours"))

        with pytest.raises(PermissionDenied):
            await agent.run("go", deps=AgentDeps(user=None))

    async def test_an_empty_reraise_set_converts_everything(self) -> None:
        """The escape hatch for a project that wants the old behaviour back."""
        from rest_framework.exceptions import PermissionDenied

        agent = self._denying_agent(
            PermissionDenied("not yours"),
            tool_failure=ToolFailureConfig(reraise=()),
        )

        result = await agent.run("go", deps=AgentDeps(user=None))

        assert _tool_returns(result)[0].outcome == "failed"

    async def test_the_set_is_a_project_decision(self) -> None:
        agent = self._denying_agent(
            LookupError("tenant missing"),
            tool_failure=ToolFailureConfig(reraise=(LookupError,)),
        )

        with pytest.raises(LookupError):
            await agent.run("go", deps=AgentDeps(user=None))

    async def test_an_ordinary_failure_is_still_absorbed(self) -> None:
        result = await self._denying_agent(RuntimeError("boom")).run(
            "go", deps=AgentDeps(user=None)
        )

        assert _tool_returns(result)[0].outcome == "failed"


async def test_the_default_set_survives_drf_not_being_installed() -> None:
    """DRF arrives with the optional extras, so the default set is resolved
    through an import that finds nothing in a slim install. Django's own denial
    is still exempt there; DRF's is just another exception nobody can raise."""
    from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
    from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

    with mock.patch.dict(sys.modules, {"rest_framework.exceptions": None}):
        policy = ToolFailurePolicy()

    async def handle(error: Exception) -> None:
        await policy.on_tool_execute_error(
            None,  # type: ignore[arg-type]
            call=ToolCallPart(tool_name="t", args={}),
            tool_def=ToolDefinition(name="t", parameters_json_schema={"type": "object"}),
            args=None,
            error=error,
        )

    with pytest.raises(DjangoPermissionDenied):
        await handle(DjangoPermissionDenied("nope"))

    with pytest.raises(ToolFailed):
        await handle(DRFPermissionDenied("nope"))
