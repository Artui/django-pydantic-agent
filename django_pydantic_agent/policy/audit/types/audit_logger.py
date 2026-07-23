from __future__ import annotations

from typing import Protocol, runtime_checkable

from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent


@runtime_checkable
class AuditLogger(Protocol):
    """Sink for tool-invocation records.

    Implementations are free to drop, sample, or forward events. The
    package ships a no-op default (``NullAuditLogger``) and a
    ``logging``-backed implementation (``LoggingAuditLogger``); projects
    supply their own by passing it to their transport's ``audit_logger=``
    argument.
    """

    def record(self, event: AuditEvent) -> None: ...


__all__ = ["AuditLogger"]
