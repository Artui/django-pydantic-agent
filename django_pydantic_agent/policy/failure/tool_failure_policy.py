"""``ToolFailurePolicy`` — a raising tool fails its call, not the whole run."""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.failure.utils import PolicyToolFailed

_logger = logging.getLogger("django_pydantic_agent.failure")


class ToolFailurePolicy(AbstractCapability[Any]):
    """Turns an unhandled tool exception into a failed result the model can read.

    Without it, a tool that raises takes the run down: the transport emits
    ``RUN_ERROR``, the turn ends, and everything the model produced is discarded
    along with every other tool result in the round. One broken integration costs
    the whole answer.

    It hangs off ``on_tool_execute_error``, which is a correctness point rather
    than a stylistic one: pydantic-ai does **not** call that hook for control-flow
    exceptions (``SkipToolExecution`` / ``CallDeferred`` / ``ApprovalRequired``),
    retry signals or failure signals. The approval interrupt the tool guard
    depends on therefore passes through untouched, and so does a ``ModelRetry``
    while the tool has retries left, where a hand-rolled ``except Exception``
    around the handler would swallow them and quietly disable the gate. The
    policy neither spends nor grants retries; what it changes is the end of a
    tool's own budget, below.

    The re-raise is ``pydantic_ai.exceptions.ToolFailed``, so the model sees a
    result marked failed rather than one reading as success. (Precisely, a
    private subclass of it: pydantic-ai's control flow treats it as a
    ``ToolFailed``, and a trace records the subclass's name as the exception
    type.) A failed result spends no retry budget, so bound a persistently
    broken tool with run-level ``UsageLimits`` rather than expecting this to
    stop the model calling it.

    **Nothing is swallowed.** An exception this converts is logged with its
    traceback to the ``django_pydantic_agent.failure`` logger first, and an
    ``AuditCapability`` in the same chain still records the failure against the
    tool that caused it. What changes is only who the failure stops. That
    logger hears only about what this converts: not a call another capability
    recovered or answered for, not a refusal passing through, and not an
    exception pydantic-ai never hands to an error hook, such as a tool's own
    ``ToolFailed``, or its ``ModelRetry`` while it has retries left.

    **It converts last.** Pinned outermost, and placed first by ``build_agent``,
    so its ``on_tool_execute_error`` is the last of every capability's to run
    (pydantic-ai runs that hook innermost first). Every other capability's error
    hook is handed the exception the tool raised, never this policy's redacted
    copy: a step recorder such as harness's ``StepPersistence`` logs the tool's
    failure, and a capability that recovers answers before anything is
    converted. What an earlier hook raised in the exception's place is that
    capability's answer, and passes through when it is one pydantic-ai gives the
    model itself: a ``ToolFailed`` already carries its own message for the
    model, and a ``ModelRetry`` spends the tool's retry budget. Once that is
    spent the run ends with ``UnexpectedModelBehavior``, which pydantic-ai
    raises after every error hook has run, so this never sees it.

    **A tool's own ``ModelRetry`` with no retries left is converted.** While
    the tool has retries left pydantic-ai never hands its ``ModelRetry`` to an
    error hook. Once they are spent it raises ``UnexpectedModelBehavior`` in
    its place from inside the call, which does reach the error hooks, so this
    logs it and converts it into a failed result like any other exception,
    where without the policy the run would end. A capability's ``ModelRetry``
    is different: pydantic-ai checks its budget outside the error hooks, so it
    still ends the run when the budget is spent. The default carries a cost,
    since the tool keeps executing on later calls and its side effects repeat,
    bounded by the model taking the failed result's "do not retry" or by
    pydantic-ai's default request limit. Naming ``UnexpectedModelBehavior`` in
    ``ToolFailureConfig.reraise`` ends the run instead, and ends it for a
    sub-agent's exhausted budget a tool propagates too.

    **An authorization refusal is exempt** and ends the run as it would without
    the policy — see ``ToolFailureConfig.reraise``, which is also how a project
    exempts more, or nothing at all.
    """

    def __init__(self, config: ToolFailureConfig | None = None) -> None:
        self._config = config if config is not None else ToolFailureConfig()
        self._reraise = (
            self._config.reraise if self._config.reraise is not None else _denial_types()
        )

    def get_ordering(self) -> CapabilityOrdering:
        """Pin the policy as an **outermost** capability.

        pydantic-ai runs ``on_tool_execute_error`` hooks innermost first, so
        the outermost one runs last: whatever else is composed sees the tool's
        exception before this converts it. ``build_agent`` also places it first
        in its list, which keeps it last among other outermost capabilities,
        since list order breaks ties within a tier.
        """
        return CapabilityOrdering(position="outermost")

    async def on_tool_execute_error(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        error: Exception,
    ) -> Any:
        if isinstance(error, self._reraise):
            # Not logged here: it is on its way to the transport intact, and a
            # traceback saying "the run continues" would be a lie about this one.
            raise error
        if isinstance(error, (ModelRetry, ToolFailed)):
            # pydantic-ai never hands a tool's own ``ModelRetry`` or
            # ``ToolFailed`` to this hook, so one arriving here was raised by an
            # earlier capability's error hook as its answer for the model.
            # Converting it would replace that answer with this policy's.
            # Each member is held by a test that fails without it:
            # ``test_another_capabilitys_retry_is_not_converted`` and
            # ``test_another_capabilitys_failed_result_is_not_converted``.
            raise error
        _logger.exception(
            "django-pydantic-agent: tool %r failed; the run continues with a failed result",
            tool_def.name,
            exc_info=error,
        )
        raise PolicyToolFailed(self._message(tool_def.name, error)) from error

    def _message(self, tool_name: str, error: Exception) -> str:
        """The model-facing text. Names the tool either way, so the model can
        route around the one that broke."""
        if self._config.include_detail:
            return f"The {tool_name} tool failed: {type(error).__name__}: {error}"
        return (
            f"The {tool_name} tool failed and returned no result. "
            "The failure has been recorded; do not retry the same call."
        )


def _denial_types() -> tuple[type[BaseException], ...]:
    """The default pass-through set: an authorization refusal, both flavours.

    Django's own and DRF's are unrelated classes — neither inherits from the
    other — and a Django project raises both, so covering one is covering half a
    boundary. DRF is optional here (it arrives with the ``[spec-tools]`` and
    ``[drf-mcp]`` extras, and drf-services' off-HTTP permission check raises its
    ``PermissionDenied``), so its class is imported at call time and simply
    absent from the set in a slim install.
    """
    denials: list[type[BaseException]] = [DjangoPermissionDenied]
    try:
        from rest_framework.exceptions import PermissionDenied
    except ImportError:
        return tuple(denials)
    denials.append(PermissionDenied)
    return tuple(denials)


__all__ = ["ToolFailurePolicy"]
