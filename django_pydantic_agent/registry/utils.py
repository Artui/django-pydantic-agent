"""Shared registry helpers: recognising the parameter pydantic-ai fills in."""

from __future__ import annotations

import inspect
import typing
from typing import Any

from pydantic_ai import RunContext


def run_context_parameter(sig: inspect.Signature) -> str | None:
    """The name of ``sig``'s leading ``RunContext`` parameter, if it has one.

    Pydantic-AI fills that parameter in itself, from the run: it is how a tool
    reaches the acting user, and it is never something the model supplies. So it
    is neither a tool argument in the derived JSON Schema nor an argument direct
    dispatch can bind from the model's payload.

    **Only the first parameter counts**, matching pydantic-ai: it takes the
    context first or not at all. A later parameter of the same type is an
    ordinary argument, wrong though it is, and hiding it would turn a signature
    pydantic-ai rejects into one this package silently accepted.

    ``sig`` must have been built with ``eval_str=True``; both callers do, and it
    is what makes the annotation a class to compare rather than a name to match.
    """
    params = list(sig.parameters.values())
    if params and _is_run_context(params[0].annotation):
        return params[0].name
    return None


def _is_run_context(annotation: Any) -> bool:
    """Whether ``annotation`` denotes a ``RunContext``, parameterised or bare.

    ``RunContext[AgentDeps]`` is a generic alias rather than the class, so the
    origin is what carries the identity. Both callers resolve annotations
    (``inspect.signature(..., eval_str=True)``), so a string never arrives here
    and a class is compared to a class rather than to a name.
    """
    target = typing.get_origin(annotation) or annotation
    return isinstance(target, type) and issubclass(target, RunContext)


__all__ = ["run_context_parameter"]
