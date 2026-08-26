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

    reraise: tuple[type[BaseException], ...] | None = None
    """Exception types that pass through untouched, ending the run as they would
    without the policy.

    ``None`` means the built-in set: an authorization refusal, in both the
    flavours a Django project raises it — ``django.core.exceptions``' and, when
    DRF is installed, ``rest_framework.exceptions``'. Pass a tuple to replace
    that set wholesale, or ``()`` to convert every exception.

    **Why a denial is not a tool failure.** A converted denial leaves the run
    alive and the model free to call the same tool on the next row, while a
    failed result stays distinguishable from a "not found" one — so a sweep over
    ids turns a permission boundary into an existence oracle inside a single
    turn. A ``ToolFailed`` spends no retry budget, so nothing bounds the sweep
    but run-level ``UsageLimits``. Refusing to run is the *answer* to a denied
    call, not a fault to route around."""


__all__ = ["ToolFailureConfig"]
