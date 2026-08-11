"""``ToolFailurePolicy`` — a raising tool fails its call, not the whole run."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig

_logger = logging.getLogger("django_pydantic_agent.failure")


class ToolFailurePolicy(AbstractCapability[Any]):
    """Turns an unhandled tool exception into a failed result the model can read.

    Without it, a tool that raises takes the run down: the transport emits
    ``RUN_ERROR``, the turn ends, and everything the model had already produced
    is discarded along with the results of every other tool in the round. One
    broken integration therefore costs the whole answer.

    The capability hangs off ``on_tool_execute_error`` rather than wrapping
    ``wrap_tool_execute``, which matters for correctness rather than style:
    Pydantic-AI does **not** call that hook for control-flow exceptions
    (``SkipToolExecution`` / ``CallDeferred`` / ``ApprovalRequired``), retry
    signals (``ToolRetryError`` from ``ModelRetry``) or failure signals
    (``ToolFailedError`` from ``ToolFailed``). So the approval interrupt the
    tool guard depends on, and the model's own retry budget, pass through
    untouched — where a hand-rolled ``except Exception`` around the handler
    would have swallowed them and quietly disabled the gate.

    It re-raises as :class:`~pydantic_ai.exceptions.ToolFailed`, the upstream
    primitive for a terminal tool failure, so the model sees a result marked
    failed rather than one that reads as success. ``ToolFailed`` also spends no
    retry budget, so bound a persistently broken tool with run-level
    ``UsageLimits`` rather than expecting this to stop the model calling it
    again.

    **Nothing is swallowed.** The exception is logged with its traceback to the
    ``django_pydantic_agent.failure`` logger, and an ``AuditCapability`` in the
    same chain still records the failure against the tool that caused it -- the
    two hooks are independent, so no ordering constraint is needed between
    them. What changes is only who the failure stops.
    """

    def __init__(self, config: ToolFailureConfig | None = None) -> None:
        self._config = config if config is not None else ToolFailureConfig()

    async def on_tool_execute_error(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        error: Exception,
    ) -> Any:
        _logger.exception(
            "django-pydantic-agent: tool %r failed; the run continues with a failed result",
            tool_def.name,
            exc_info=error,
        )
        raise ToolFailed(self._message(tool_def.name, error)) from error

    def _message(self, tool_name: str, error: Exception) -> str:
        """The model-facing text. Names the tool either way, so the model can
        route around the one that broke rather than only knowing that something
        did."""
        if self._config.include_detail:
            return f"The {tool_name} tool failed: {type(error).__name__}: {error}"
        return (
            f"The {tool_name} tool failed and returned no result. "
            "The failure has been recorded; do not retry the same call."
        )


__all__ = ["ToolFailurePolicy"]
