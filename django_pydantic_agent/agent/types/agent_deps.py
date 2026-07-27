from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# NOT frozen — and this is the one place in the package where that is deliberate.
# Pydantic-AI's ``StateHandler`` protocol (``pydantic_ai.ui``) requires a *settable*
# ``state``: its UI adapter assigns ``deps.state = state`` directly when a run
# carries ``RunAgentInput.state``. A frozen dataclass raises ``FrozenInstanceError``
# there. The protocol's own comment says it uses ``dataclasses.replace``, but the
# adapter does not — verified against the assignment in ``pydantic_ai/ui/_adapter.py``.
# Deps are per-run and never shared, so the mutability is contained.
@dataclass
class AgentDeps:
    """Per-run dependencies handed to the agent as ``ctx.deps``.

    Pydantic-AI's own seam for threading request-scoped values into a run:
    every tool, toolset and capability reads them off ``RunContext.deps``
    instead of closing over a request. That closure is what this type replaces —
    it is why the agent had to be rebuilt per request, and why the acting user
    could not be read through the upstream default.

    ``djangorestframework-pydantic-ai``'s ``SpecToolset`` already reads
    ``ctx.deps.user`` as its default user extractor, so a run given these deps
    binds the user natively with nothing passed at the call site.

    Projects wanting more per-run context subclass this and set
    ``AgentConfig.deps_factory``; the fields below are the two the framework
    itself reads.
    """

    user: Any = None
    """The acting Django user (``request.user``), or ``None`` for an
    unauthenticated run. ``Any`` because it is a Django boundary — it may be a
    ``User``, an ``AnonymousUser``, or a project's own model."""

    state: Any = None
    """AG-UI shared state for this run.

    Present so the deps satisfy ``StateHandler``: the UI adapter checks
    ``isinstance(deps, StateHandler)`` and, when it matches, validates the
    client's ``RunAgentInput.state`` into this field — otherwise it drops the
    state and emits a ``UserWarning`` naming the deps type. So a run now
    *receives* client state instead of discarding it.

    **Inbound only, for now.** Nothing emits ``STATE_SNAPSHOT`` / ``STATE_DELTA``
    back; a tool has to return those itself as ``ToolReturn`` metadata, and
    wiring that end-to-end is its own piece of work.

    Left ``None`` (rather than typed) so the default is transport-agnostic: with
    a non-``BaseModel`` value the adapter passes the raw mapping through
    unvalidated. To get validation, seed it with an instance of a Pydantic model
    — the adapter validates the incoming state against ``type(deps.state)``.
    """


__all__ = ["AgentDeps"]
