from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# NOT frozen, the one deliberate exception in this package. Pydantic-AI's
# ``StateHandler`` protocol needs a settable ``state``: its UI adapter assigns
# ``deps.state = state`` directly, and a frozen dataclass raises
# ``FrozenInstanceError`` there. (The protocol's own comment claims
# ``dataclasses.replace``; the adapter does not use it.) Deps are per-run and
# never shared, so the mutability is contained.
@dataclass
class AgentDeps:
    """Per-run dependencies handed to the agent as ``ctx.deps``.

    Pydantic-AI's seam for threading request-scoped values into a run: tools,
    toolsets and capabilities read them off ``RunContext.deps`` instead of
    closing over a request, which is what lets one agent serve many requests.
    ``djangorestframework-pydantic-ai``'s ``SpecToolset`` already reads
    ``ctx.deps.user`` as its default user extractor, so a run given these deps
    binds the acting user with nothing passed at the call site.

    For more per-run context, subclass this and set ``AgentConfig.deps_factory``;
    the fields below are the ones the framework itself reads.
    """

    user: Any = None
    """The acting Django user (``request.user``), or ``None`` for an
    unauthenticated run. ``Any`` because it is a Django boundary: a ``User``, an
    ``AnonymousUser``, or a project's own model."""

    ip_address: str | None = None
    """The client IP this run was driven from, stamped onto every audit event
    :class:`~django_pydantic_agent.policy.audit.audit_capability.AuditCapability`
    records. Per-run, so it belongs here rather than on the capability's
    constructor — a transport that closes over an IP has an agent good for one
    request. The constructor argument remains the right home for a value that is
    genuinely fixed for the endpoint, and an unset run falls back to it."""

    state: Any = None
    """AG-UI shared state for this run, **inbound only**.

    Present so the deps satisfy ``StateHandler``: the UI adapter validates the
    client's ``RunAgentInput.state`` into this field, and drops the state with a
    ``UserWarning`` when the deps type does not match. Nothing emits
    ``STATE_SNAPSHOT`` / ``STATE_DELTA`` back — a tool has to return those itself
    as ``ToolReturn`` metadata.

    ``None`` keeps the default transport-agnostic: the adapter passes a raw
    mapping through unvalidated. Seed it with a Pydantic model instance to get
    validation, which the adapter runs against ``type(deps.state)``.
    """


__all__ = ["AgentDeps"]
