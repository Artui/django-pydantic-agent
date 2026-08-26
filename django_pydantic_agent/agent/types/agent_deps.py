from __future__ import annotations

from collections.abc import Callable
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

    For more per-run context, subclass this and build it in your transport's
    ``deps_factory`` (``AGUIServer(deps_factory=...)`` for django-ag-ui); the
    fields below are the ones the framework itself reads. The factory belongs to
    the transport rather than to ``AgentConfig`` because the deps are per-request
    and the config is not — an agent built once serves every run.
    """

    user: Any
    """The acting Django user (``request.user``), or ``None`` for an
    unauthenticated run. ``Any`` because it is a Django boundary: a ``User``, an
    ``AnonymousUser``, or a project's own model.

    **Required, with no default.** Pydantic-AI types ``deps`` as
    ``AgentDepsT = None`` and never validates it, so nothing downstream would
    catch a run built without one. A registry tool does not fail closed the way a
    spec tool does: it runs with no user context, audit records it with the
    fields it has, and the answer reads like any other. Saying ``user=None`` is
    one word, and it is a decision rather than an omission."""

    ip_address: str | None = None
    """The client IP this run was driven from, stamped onto every audit event
    [`AuditCapability`][django_pydantic_agent.AuditCapability]
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

    progress: Callable[..., None] | None = None
    """Where a spec's ``progress(...)`` calls go for this run, or ``None``.

    A drf-services ``ProgressReporter``: a callable
    ``(progress, *, total, message, meta) -> None``. ``SpecToolset`` reads this
    field by name and forwards it into the dispatch pool, so a service that
    declares a ``progress`` parameter reports to whatever the caller passed.
    Typed structurally rather than as the Protocol, which lives in an optional
    dependency.

    **Nothing here constructs one.** Where a report should go is a transport's
    decision — an SSE frame, a task record, a log line — and a substrate that
    picked one would have chosen a transport it does not own. ``None`` is the
    honest default and costs nothing: drf-services substitutes its no-op, so a
    service declaring ``progress`` runs unchanged whether or not anyone is
    listening. A transport that wants the reports on the wire supplies the sink
    from its ``deps_factory`` and emits the events itself."""


__all__ = ["AgentDeps"]
