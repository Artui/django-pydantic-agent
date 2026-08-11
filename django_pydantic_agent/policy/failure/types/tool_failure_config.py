from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolFailureConfig:
    """Resolved policy for what an unhandled tool exception does to a run.

    Tunes the
    :class:`~django_pydantic_agent.policy.failure.tool_failure_policy.ToolFailurePolicy`
    capability. **On by default**, because the behaviour it replaces is the
    worse one: without it a single raising tool ends the whole run, discarding
    the answer the model had already assembled and every other tool result in
    the turn.

    ``include_detail`` is separately off by default, and the split is the point.
    Whether the run survives is a reliability question; whether the exception's
    text reaches the model is a disclosure one. A traceback message can carry a
    query, a path or a credential, and anything handed to the model is also
    handed to whatever renders the transcript. The operator's copy is never
    redacted: the full exception goes to the audit logger and the
    ``django_pydantic_agent.failure`` Python logger regardless.
    """

    enabled: bool = True
    """Whether the ``ToolFailurePolicy`` capability is composed into the agent.
    Set ``False`` to restore the fail-the-run behaviour."""

    include_detail: bool = False
    """Whether the model-facing failure message carries the exception type and
    text. Off by default: an exception message is written for an operator, not
    for a model or the browser that will render its answer."""


__all__ = ["ToolFailureConfig"]
