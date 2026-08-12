from __future__ import annotations

from typing import Protocol, runtime_checkable

from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent


@runtime_checkable
class AuditLogger(Protocol):
    """Sink for tool-invocation records.

    An implementation may drop, sample or forward events. The package ships
    ``NullAuditLogger`` and ``LoggingAuditLogger``; a project passes its own to
    its transport's ``audit_logger=``.
    """

    def record(self, event: AuditEvent) -> None: ...


__all__ = ["AuditLogger"]
