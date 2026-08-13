from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolFailureConfig:
    """Resolved policy for what an unhandled tool exception does to a run.

    Tunes the
    [`ToolFailurePolicy`][django_pydantic_agent.ToolFailurePolicy]
    capability. On by default, because without it one raising tool ends the whole
    run, discarding the answer the model had assembled and every other tool
    result in the turn.

    The two flags default opposite ways because they answer different questions.
    Whether the run survives is reliability; whether the exception's text reaches
    the model is disclosure, since a message can carry a query, a path or a
    credential, and whatever the model sees also reaches whatever renders the
    transcript. The operator's copy is never redacted either way: the full
    exception goes to the audit logger and the Python logger regardless.
    """

    enabled: bool = True
    """Whether the ``ToolFailurePolicy`` capability is composed into the agent.
    Set ``False`` to restore the fail-the-run behaviour."""

    include_detail: bool = False
    """Whether the model-facing failure message carries the exception type and
    text. An exception message is written for an operator, not for a model or
    the browser that renders its answer."""


__all__ = ["ToolFailureConfig"]
