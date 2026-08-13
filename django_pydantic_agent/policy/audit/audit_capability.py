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
                error=f"{type(error).__name__}: {error}",
            )
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


__all__ = ["AuditCapability"]
