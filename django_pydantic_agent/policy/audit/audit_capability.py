"""``AuditCapability`` — audit every tool execution through one lifecycle hook."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    WrapToolExecuteHandler,
)
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger

_fallback_logger = logging.getLogger("django_pydantic_agent.audit")


class AuditCapability(AbstractCapability[Any]):
    """Records every tool execution to an
    [`AuditLogger`][django_pydantic_agent.AuditLogger] sink.

    A Pydantic-AI capability on the ``wrap_tool_execute`` lifecycle hook, so it
    times and records **every** tool the agent runs: registry tools, the drf-mcp
    and spec bridges, attachment and skill tools alike.

    Recording is **non-raising**. A sink that throws is caught and logged to the
    ``django_pydantic_agent.audit`` Python logger, so a broken audit backend
    costs audit records rather than the run.

    A failure is recorded as ``Type: message`` of the exception that reaches
    the hook, with one exception. A ``pydantic_ai.exceptions.ToolFailed`` that
    a capability hook raised ``from`` the tool's exception, as
    [`ToolFailurePolicy`][django_pydantic_agent.ToolFailurePolicy] does, is
    recorded as that cause. The ``ToolFailed`` is the copy written for the
    model, which the policy redacts unless ``include_detail``, and this record
    is the operator's, which is never redacted. A ``ToolFailed`` with no cause,
    such as a ``before_tool_execute`` veto, is recorded as itself. A
    ``ToolFailed`` the tool raises itself never arrives as one: pydantic-ai
    converts it into a ``ToolFailedError`` carrying the same message before any
    capability sees it, so that message is what is recorded, never its cause.
    The exception the run sees is untouched in every case.

    **What this hook encloses is pydantic-ai's decision**, and it changed in
    2.54, where a ``wrap_*`` hook began enclosing every other capability's
    hooks for the same tool call. Pinned outermost, audit on 2.54 sees what
    those hooks made of the call, where earlier releases showed it the tool
    alone:

    - A failure another capability's ``on_tool_execute_error`` recovers from
      is recorded as a success with no error; earlier, as the failure.
    - A ``before_tool_execute`` that vetoes the call is recorded as a failure,
      where earlier nothing was recorded. A ``SkipToolExecution``, which
      pydantic-ai-harness's guardrails and tool-call judge raise, carries a
      result rather than a message, so it reads ``SkipToolExecution: ``.
    - ``arguments_repr`` holds the arguments before another capability's
      ``before_tool_execute`` rewrites them, and ``result_size`` measures the
      result after its ``after_tool_execute``. Earlier it was the reverse.
    - ``duration_ms`` includes the time spent in those hooks.

    These are upstream ordering effects, and the ``ToolFailed`` unwrap above is
    the only one this class compensates for.

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

    def get_ordering(self) -> CapabilityOrdering:
        """Pin audit as the **outermost** capability in the chain.

        Its ``wrap_tool_execute`` has to surround every other capability's
        execution hooks so the tool is recorded whatever else composes the run.
        Declaring it here rather than relying on list order at the
        ``build_agent`` call site keeps that true however the capabilities are
        inserted, since pydantic-ai sorts by these constraints.
        """
        return CapabilityOrdering(position="outermost")

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """Time the tool, record the outcome, and hand the result back as is.

        Pinned outermost, this wrapper encloses every other capability's
        ``wrap_tool_execute``, and from pydantic-ai 2.54 their
        ``before_tool_execute``, ``after_tool_execute`` and
        ``on_tool_execute_error`` as well. So what arrives here may already be
        another capability's account of the call rather than the tool's. The
        class docstring says what is recorded in each case, and which of those
        differences are upstream's rather than this class's.
        """
        started = time.perf_counter()
        ip_address = self._resolve_ip_address(ctx)
        try:
            result = await handler(args)
        except Exception as error:
            self._record(
                tool_def.name,
                args,
                started,
                ip_address=ip_address,
                success=False,
                error=_operator_error(error),
            )
            # The caught error, never the unwrapped cause: what the model and
            # the transport see is another capability's decision, not audit's.
            raise
        self._record(
            tool_def.name,
            args,
            started,
            ip_address=ip_address,
            success=True,
            result_size=len(str(result)),
        )
        return result

    def _resolve_ip_address(self, ctx: RunContext[Any]) -> str | None:
        """This run's client IP: ``deps.ip_address``, else the constructed one.

        ``getattr`` because the deps type is the host's to choose: a project's
        own deps class, or ``None``, has no such field, and that is the fallback
        case rather than an error.
        """
        from_deps = getattr(ctx.deps, "ip_address", None)
        return from_deps if from_deps is not None else self._ip_address

    def _record(
        self,
        name: str,
        args: dict[str, Any],
        started: float,
        *,
        ip_address: str | None,
        success: bool,
        error: str | None = None,
        result_size: int | None = None,
    ) -> None:
        event = AuditEvent(
            tool_name=name,
            arguments_repr=json.dumps(args, default=str, sort_keys=True),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            success=success,
            error=error,
            result_size=result_size,
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


def _operator_error(error: Exception) -> str:
    """The failure as the operator's record names it: ``Type: message``.

    A ``ToolFailed`` raised ``from`` an exception is recorded as that cause.
    ``ToolFailurePolicy`` raises exactly that from ``on_tool_execute_error``,
    with text written for the model and redacted unless ``include_detail``, and
    once pydantic-ai wraps error hooks inside ``wrap_tool_execute`` this is the
    exception audit catches. Recording it as caught would put the model's copy
    in the one record meant to keep the cause.

    Only a capability hook's ``ToolFailed`` arrives here as one. A
    ``ToolFailed`` a tool raises itself, ``from e`` or not (the drf-mcp bridge
    raises one for a server's refusal), is converted by pydantic-ai's tool
    manager into a ``ToolFailedError`` carrying the same message before any
    capability sees it. That is not a ``ToolFailed``, so it is recorded as
    caught, by its message and never by ``e``. A ``ToolFailed`` with no cause,
    such as a ``before_tool_execute`` veto, has nothing else to name and is
    recorded as itself.

    Each condition is one branch arc with the other, so coverage cannot see
    either go missing. These tests do:

    - ``isinstance(error, ToolFailed)``:
      ``test_only_a_tool_failed_is_unwrapped_to_its_cause``, where any other
      chained exception would be recorded as its cause, and
      ``test_a_tools_own_tool_failed_is_recorded_as_pydantic_ai_delivers_it``,
      where the ``ToolFailedError`` would be recorded as the tool's
      ``ToolFailed``.
    - ``isinstance(cause, Exception)``:
      ``test_a_tool_failed_with_no_cause_is_recorded_as_itself``, where a
      missing cause would be recorded as ``NoneType: None``.
    """
    cause = error.__cause__
    if isinstance(error, ToolFailed) and isinstance(cause, Exception):
        error = cause
    return f"{type(error).__name__}: {error}"


__all__ = ["AuditCapability"]
