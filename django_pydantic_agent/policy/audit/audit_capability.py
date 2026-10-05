"""``AuditCapability`` — one record per tool execution, describing the tool's own."""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger

_fallback_logger = logging.getLogger("django_pydantic_agent.audit")


class AuditCapability(AbstractCapability[Any]):
    """Records every tool execution to an
    [`AuditLogger`][django_pydantic_agent.AuditLogger] sink.

    A Pydantic-AI capability on the tool-execution hooks, so it records
    **every** tool the agent runs: registry tools, the drf-mcp and spec
    bridges, attachment and skill tools alike.

    **One record per execution, describing the tool's own execution**, whatever
    other capabilities are composed and on every supported pydantic-ai:

    - ``arguments_repr`` holds the arguments the tool received, after every
      other capability's ``before_tool_execute`` has rewritten them.
    - ``success`` and ``error`` describe what the tool did. ``error`` is
      ``Type: message`` of the exception the tool raised, before any capability
      converts it (as [`ToolFailurePolicy`][django_pydantic_agent.ToolFailurePolicy]
      does) or recovers from it with an ``on_tool_execute_error`` that returns a
      value. A recovered failure is still recorded as the failure: recovering is
      a decision about the run, not about the tool. The exceptions pydantic-ai
      routes past every error hook are recorded as they arrive: a tool's own
      ``ToolFailed`` as the ``ToolFailedError`` pydantic-ai converts it into,
      with the same message, and ``ModelRetry`` as ``ToolRetryError``.
    - ``result_size`` is the length of the tool's own result, before any
      ``after_tool_execute`` rewrites it.
    - ``duration_ms`` spans the tool alone: from the last moment audit sees
      before the tool runs, which is after every other capability's
      ``before_tool_execute``, to the first moment it sees the outcome, which
      is before every other capability's ``on_tool_execute_error`` or
      ``after_tool_execute``. Other capabilities' hooks are not in it.

    **A call that never reaches the tool produces no record.** A
    ``before_tool_execute`` that stops the call, such as the
    ``SkipToolExecution`` pydantic-ai-harness's guardrails and tool-call judge
    raise, and a destructive call the [`ToolGuard`][django_pydantic_agent.ToolGuard]
    holds for approval are not executions. The approved call, when it runs, is
    recorded like any other.

    **How it gets the same record on every release.** pydantic-ai 2.54 moved
    every capability's ``before_tool_execute``, ``on_tool_execute_error`` and
    ``after_tool_execute`` inside the ``wrap_tool_execute`` chain, where
    earlier releases ran them around it, so no wrapper position sees the raw
    outcome on both. Audit is pinned **innermost**, which makes its
    ``before_tool_execute`` the last to run and its ``on_tool_execute_error``
    and ``after_tool_execute`` the first, and observes the tool from all four
    hooks. Its wrapper is the only one that writes the record, once, as it
    exits, preferring what the hooks captured over what it saw itself. Before
    2.54 the innermost wrapper encloses the tool alone and what it sees is
    already the raw outcome; from 2.54 it encloses every hook, and the
    captures are what keep the record the tool's.

    **Compose it after any other innermost capability.** ``build_agent``
    appends it after everything in ``AgentConfig.capabilities``, so it runs
    inside harness's guardrail and tool-call judge, which are innermost too,
    and their vetoes never reach it. Composed by hand ahead of one, a veto
    that capability raises would be recorded as a failure on 2.54.

    Recording is **non-raising**. A sink that throws is caught and logged to the
    ``django_pydantic_agent.audit`` Python logger, so a broken audit backend
    costs audit records rather than the run. Audit's own error hook re-raises
    what it is handed, and the exception the run sees is never changed by it.

    Args:
        logger: The sink each [`AuditEvent`][django_pydantic_agent.AuditEvent]
            is recorded to.
        ip_address: Fallback client IP, used only when the run's deps carry no
            ``ip_address``. Per-run deps come first because a constructor
            argument is per-agent: taking the IP from it alone forces a fresh
            agent per request, and building once anyway fails silently, with
            every record carrying the IP of whoever arrived first.
        organization_id: Org scope stamped onto every event, for a multi-tenant
            host.
    """

    def __init__(
        self,
        logger: AuditLogger,
        *,
        ip_address: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        self._logger = logger
        self._ip_address = ip_address
        self._organization_id = organization_id
        # What this capability has seen of the tool call in flight. A context
        # variable, because pydantic-ai runs every tool call in its own asyncio
        # task, so parallel calls in one run and concurrent runs of one agent
        # each read their own value with no shared table to clean up: the state
        # goes with the call's task, including when a hook never runs. Not a
        # table keyed by ``tool_call_id``, which the model chooses and
        # ``TestModel`` repeats in every run. One per instance, so two audit
        # capabilities in one chain never read each other's.
        self._execution: ContextVar[_Execution | None] = ContextVar(
            "django_pydantic_agent.audit.execution", default=None
        )

    def get_ordering(self) -> CapabilityOrdering:
        """Pin audit as an **innermost** capability.

        pydantic-ai runs ``before_tool_execute`` hooks outermost first and
        ``on_tool_execute_error`` / ``after_tool_execute`` hooks innermost
        first, so innermost is the one position whose hooks sit directly either
        side of the tool: after every other capability's argument rewrite and
        veto, and before any other capability converts, recovers or rewrites
        the outcome. Declared here rather than left to list order at the
        ``build_agent`` call site, since pydantic-ai sorts by these constraints.
        """
        return CapabilityOrdering(position="innermost")

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """Write the call's one record on the way out, and hand the result back as is.

        What this wrapper sees depends on the release: the tool alone before
        2.54, every capability's hooks around it from 2.54. Either way the
        hooks below have recorded the tool's side first where they could, and
        ``settle`` keeps the first account it is given, so what is written is
        the tool's. A call whose ``before_tool_execute`` never ran here never
        reached the tool, and is not recorded.

        Every entry starts a fresh execution, because pydantic-ai lets an outer
        wrapper call its handler more than once and each call runs the tool
        again. Before 2.54 ``before_tool_execute`` runs once, ahead of every
        wrapper, so whether the call reached the tool is carried over from what
        it left in this context. From 2.54 it runs again inside each entry and
        says so itself. The reset on the way out is what keeps one entry's
        verdict from carrying into the next one there:
        ``test_a_wrapper_that_runs_the_tool_twice_gets_one_record_per_run``.
        """
        execution = _Execution(call, reached=self._current(call).reached)
        token = self._execution.set(execution)
        execution.begin(args)
        ip_address = self._resolve_ip_address(ctx)
        try:
            try:
                result = await handler(args)
            except Exception as error:
                execution.settle(error=error)
                self._record(tool_def.name, execution, ip_address=ip_address)
                raise
            execution.settle(result=result)
            self._record(tool_def.name, execution, ip_address=ip_address)
            return result
        finally:
            self._execution.reset(token)

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Note that the call reached the tool, with the arguments it gets.

        Innermost, so this runs after every other capability's
        ``before_tool_execute``, and nothing else stands between it and the
        tool. Bound to the context here as well as in the wrapper because before
        2.54 this hook runs first, and the wrapper reads it from there.
        """
        execution = self._current(call)
        self._execution.set(execution)
        execution.reached = True
        execution.begin(args)
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
        """Keep the tool's own exception, then pass it on unchanged.

        Innermost, so this is the first error hook to run, before any other
        capability converts or recovers. Re-raising is what keeps the chain
        going: the next capability's hook is handed the same exception.
        """
        self._current(call).settle(error=error)
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
        """Keep the tool's own result, then pass it on unchanged.

        Innermost, so this is the first result hook to run, before any other
        capability rewrites the result. After a recovery it is handed the
        recovered value instead, which ``settle`` ignores: the error hook
        settled the call first.
        """
        self._current(call).settle(result=result)
        return result

    def _current(self, call: ToolCallPart) -> _Execution:
        """The execution this context holds for ``call``, else a fresh one.

        Both conditions are one branch arc, so coverage cannot see either go
        missing. These tests do:

        - ``execution is not None``: every recording test, starting with
          ``test_records_toolset_tools_not_just_registry_tools``, which would
          read ``.call`` off ``None`` in a fresh context.
        - ``execution.call is call``:
          ``test_a_nested_run_sharing_the_capability_records_its_own_calls``,
          where a tool runs an agent composed with this same capability, so the
          inner calls start in a context that already holds the outer one's.
          From 2.54 an inner veto would inherit the outer call's verdict and be
          recorded; before it, the inner hooks would settle the outer record.
          Identity rather than ``tool_call_id``, which the model chooses.
        """
        execution = self._execution.get()
        if execution is not None and execution.call is call:
            return execution
        return _Execution(call)

    def _resolve_ip_address(self, ctx: RunContext[Any]) -> str | None:
        """This run's client IP: ``deps.ip_address``, else the constructed one.

        ``getattr`` because the deps type is the host's to choose: a project's
        own deps class, or ``None``, has no such field, and that is the fallback
        case rather than an error.
        """
        from_deps = getattr(ctx.deps, "ip_address", None)
        return from_deps if from_deps is not None else self._ip_address

    def _record(self, name: str, execution: _Execution, *, ip_address: str | None) -> None:
        if not execution.reached:
            # A ``before_tool_execute`` ahead of audit's stopped the call, so
            # the tool never ran. Before 2.54 the wrapper is not even entered
            # then; this keeps 2.54 the same.
            return
        error = execution.error
        event = AuditEvent(
            tool_name=name,
            arguments_repr=json.dumps(execution.args, default=str, sort_keys=True),
            duration_ms=(execution.ended - execution.started) * 1000.0,
            success=error is None,
            error=None if error is None else f"{type(error).__name__}: {error}",
            result_size=len(str(execution.result)) if error is None else None,
            organization_id=self._organization_id,
            ip_address=ip_address,
        )
        try:
            self._logger.record(event)
        except Exception:
            _fallback_logger.exception(
                "audit logger %r raised while recording %r; event dropped",
                type(self._logger).__name__,
                name,
            )


class _Execution:
    """What one audit capability has observed of one tool execution.

    Each hook adds what it saw, and only the wrapper reads it, so the order the
    installed pydantic-ai runs them in decides nothing but which observation is
    nearest the tool.
    """

    def __init__(self, call: ToolCallPart, *, reached: bool = False) -> None:
        self.call = call
        # Whether audit's ``before_tool_execute`` ran for this call, which is
        # the last moment before the tool: a veto ahead of it means no record.
        self.reached = reached
        self.args: dict[str, Any] = {}
        self.started = 0.0
        self.settled = False
        self.ended = 0.0
        self.error: Exception | None = None
        self.result: Any = None

    def begin(self, args: dict[str, Any]) -> None:
        """The tool is about to run with ``args``.

        The wrapper and ``before_tool_execute`` both call this, and the later
        of the two is nearer the tool, so each call replaces the last.
        """
        self.args = args
        self.started = time.perf_counter()

    def settle(self, *, error: Exception | None = None, result: Any = None) -> None:
        """The tool's outcome, if no earlier observation has settled it.

        The first account is the one nearest the tool: audit's own error or
        result hook where pydantic-ai calls one, else the wrapper.
        """
        if self.settled:
            return
        self.settled = True
        self.ended = time.perf_counter()
        self.error = error
        self.result = result


__all__ = ["AuditCapability"]
