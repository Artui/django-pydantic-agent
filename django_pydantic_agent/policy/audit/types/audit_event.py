from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    """A single tool invocation as seen by the audit logger.

    Arguments are stored as a string, typically JSON, to keep records cheap to
    serialize and to discourage retaining sensitive raw values.

    One run-level record rides this shape: a client disconnecting mid-run is
    recorded as ``tool_name="agent.run"``, ``success=False`` and an ``error``
    starting ``"cancelled:"``, so a sink can tell cancelled runs apart without
    widening the [`AuditLogger`][django_pydantic_agent.AuditLogger] protocol.
    """

    tool_name: str
    arguments_repr: str
    duration_ms: float
    success: bool
    error: str | None = None
    result_size: int | None = None

    organization_id: str | None = None
    """Multi-tenant scope of the acting user. ``None`` at this layer; a custom
    [`AuditLogger`][django_pydantic_agent.AuditLogger] fills it from its own
    tenancy model."""

    target_type: str | None = None
    """Kind of domain object the call acted on. ``None`` at this layer, where
    tool arguments are domain-opaque; a sink that knows its tools can
    classify them."""

    target_id: str | None = None
    """Identifier of the acted-on object, paired with ``target_type``."""

    ip_address: str | None = None
    """Client IP of the request that drove the run, when the view knows it."""


__all__ = ["AuditEvent"]
