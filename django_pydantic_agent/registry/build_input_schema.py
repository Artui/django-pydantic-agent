from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from typing import Any

from django_pydantic_agent.constants import (
    X_CATEGORY_KEY,
    X_CONFIRM_KEY,
    X_DESTRUCTIVE_KEY,
    X_SUMMARY_KEY,
    ToolCategory,
)
from django_pydantic_agent.registry.utils import run_context_parameter


def build_input_schema(
    fn: Callable[..., Any],
    *,
    destructive: bool = False,
    category: ToolCategory = ToolCategory.OTHER,
    confirm: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """Derive a JSON Schema object from ``fn``'s parameters.

    Covers ``str``, ``int``, ``float``, ``bool``, ``list[T]``, ``dict[str,
    Any]`` and ``X | None`` unions. Anything richer falls back to an empty
    fragment, imposing no type constraint but staying wire-valid.

    ``destructive`` / ``category`` / ``confirm`` / ``summary`` are stamped at the
    schema root as the matching ``x-*`` extension keys, which AG-UI passes
    through verbatim to the client.

    A leading ``ctx: RunContext[...]`` parameter is **not** an argument and is
    left out. Pydantic-AI fills it from the run — it is how a tool reaches the
    acting user — so advertising it would ask the model to invent a value for
    something it cannot supply.
    """
    # `eval_str=True` resolves string annotations while preserving them verbatim.
    # Unlike `typing.get_type_hints` it does NOT apply the implicit-`Optional`
    # wrapping Python 3.10 adds to a parameter defaulting to `None`, so the
    # derived schema is identical across Python versions.
    sig = inspect.signature(fn, eval_str=True)
    context_parameter = run_context_parameter(sig)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name == context_parameter or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = param.annotation
        hint = Any if annotation is inspect.Parameter.empty else annotation
        properties[name] = _hint_to_schema(hint)
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        X_CATEGORY_KEY: category.value,
    }
    if destructive:
        schema[X_DESTRUCTIVE_KEY] = True
    if confirm is not None:
        schema[X_CONFIRM_KEY] = confirm
    if summary is not None:
        schema[X_SUMMARY_KEY] = summary
    if required:
        schema["required"] = required
    return schema


def _hint_to_schema(hint: Any) -> dict[str, Any]:
    # A bare ``None`` annotation means ``type(None)``, but `eval_str` leaves it
    # as the ``None`` object where `get_type_hints` would not.
    if hint is None:
        hint = type(None)
    if hint is Any:
        return {}
    origin = typing.get_origin(hint)
    if origin is None:
        return _scalar_schema(hint)
    if origin in (list, tuple, set, frozenset):
        args = typing.get_args(hint)
        items = _hint_to_schema(args[0]) if args else {}
        return {"type": "array", "items": items}
    if origin is dict:
        return {"type": "object"}
    if origin is typing.Union or origin is types.UnionType:
        # ``Union[T]`` collapses to ``T`` in Python, so a union here always
        # has >= 2 distinct args. Exactly one non-``None`` arg means the
        # ``X | None`` shape; expose that as a nullable scalar.
        non_none = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(non_none) == 1:
            return {**_hint_to_schema(non_none[0]), "nullable": True}
        return {"anyOf": [_hint_to_schema(a) for a in non_none]}
    return {}


def _scalar_schema(hint: Any) -> dict[str, Any]:
    # Bool is a subclass of int — check bool first.
    if hint is bool:
        return {"type": "boolean"}
    if hint is str:
        return {"type": "string"}
    if hint is int:
        return {"type": "integer"}
    if hint is float:
        return {"type": "number"}
    if hint is type(None):
        return {"type": "null"}
    if hint in (list, tuple, set, frozenset):
        return {"type": "array", "items": {}}
    if hint is dict:
        return {"type": "object"}
    return {}


__all__ = ["build_input_schema"]
