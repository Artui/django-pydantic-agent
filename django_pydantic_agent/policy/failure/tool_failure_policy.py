"""``ToolFailurePolicy`` — a raising tool fails its call, not the whole run."""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
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
    ``RUN_ERROR``, the turn ends, and everything the model produced is discarded
    along with every other tool result in the round. One broken integration costs
    the whole answer.

    It hangs off ``on_tool_execute_error``, which is a correctness point rather
    than a stylistic one: pydantic-ai does **not** call that hook for control-flow
    exceptions (``SkipToolExecution`` / ``CallDeferred`` / ``ApprovalRequired``),
    retry signals or failure signals. The approval interrupt the tool guard
    depends on and the model's retry budget therefore pass through untouched,
    where a hand-rolled ``except Exception`` around the handler would swallow
    them and quietly disable the gate.

    The re-raise is ``pydantic_ai.exceptions.ToolFailed``, so the model sees a
    result marked failed rather than one reading as success. That spends no
    retry budget, so bound a persistently broken tool with run-level
    ``UsageLimits`` rather than expecting this to stop the model calling it.

    **Nothing is swallowed.** The exception is logged with its traceback to the
    ``django_pydantic_agent.failure`` logger, and an ``AuditCapability`` in the
    same chain still records the failure against the tool that caused it. What
    changes is only who the failure stops.

    **An authorization refusal is exempt** and ends the run as it would without
    the policy — see ``ToolFailureConfig.reraise``, which is also how a project
    exempts more, or nothing at all.
    """

    def __init__(self, config: ToolFailureConfig | None = None) -> None:
        self._config = config if config is not None else ToolFailureConfig()
        self._reraise = (
            self._config.reraise if self._config.reraise is not None else _denial_types()
        )

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
        _logger.exception(
            "django-pydantic-agent: tool %r failed; the run continues with a failed result",
            tool_def.name,
            exc_info=error,
        )
        raise ToolFailed(self._message(tool_def.name, error)) from error

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
