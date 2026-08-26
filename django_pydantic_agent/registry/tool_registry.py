from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any

from django_pydantic_agent.registry.build_input_schema import build_input_schema
from django_pydantic_agent.registry.types.tool_binding import ToolBinding
from django_pydantic_agent.registry.types.tool_spec import ToolSpec
from django_pydantic_agent.registry.utils import run_context_parameter


class ToolRegistry:
    """An ordered, named collection of server-side tools.

    State lives on the instance — a transport holds one, tests build a fresh one
    per scenario. Each tool's JSON Schema is derived once at registration, and
    either sync or async callables can be dispatched.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, ToolBinding] = {}

    def register(self, spec: ToolSpec) -> ToolBinding:
        """Register ``spec`` and return its binding.

        Raises:
            ValueError: when ``spec.name`` is already registered.
        """
        if spec.name in self._bindings:
            raise ValueError(f"tool {spec.name!r} already registered")
        binding = ToolBinding(
            spec=spec,
            input_schema=build_input_schema(
                spec.fn,
                destructive=spec.destructive,
                category=spec.category,
                confirm=spec.confirm,
                summary=spec.summary,
            ),
        )
        self._bindings[spec.name] = binding
        return binding

    def __iter__(self) -> Iterator[ToolBinding]:
        return iter(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings

    def get(self, name: str) -> ToolBinding:
        """Return the binding for ``name`` or raise ``KeyError``."""
        try:
            return self._bindings[name]
        except KeyError as e:
            raise KeyError(f"tool {name!r} is not registered") from e

    def call(self, name: str, arguments: dict[str, Any], *, ctx: Any = None) -> Any:
        """Dispatch a sync call to the registered tool.

        ``arguments`` is the model's payload and nothing else. A tool declaring a
        leading ``ctx: RunContext[...]`` gets it from ``ctx`` here, the way
        pydantic-ai would supply it from the run; passing one to a tool that
        takes none is harmless.

        Refuses coroutine functions to avoid silently returning an
        un-awaited coroutine. Use
        [`acall`][django_pydantic_agent.ToolRegistry.acall] for async tools.

        Raises:
            TypeError: when the tool is async, or declares a ``RunContext``
                parameter and no ``ctx`` was given.
        """
        fn = self.get(name).spec.fn
        if inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"tool {name!r} is async; use ToolRegistry.acall instead",
            )
        return _invoke(fn, name, arguments, ctx)

    async def acall(self, name: str, arguments: dict[str, Any], *, ctx: Any = None) -> Any:
        """Dispatch an async call; transparently awaits sync callables.

        ``ctx`` is bound exactly as in
        [`call`][django_pydantic_agent.ToolRegistry.call].
        """
        fn = self.get(name).spec.fn
        if inspect.iscoroutinefunction(fn):
            return await _ainvoke(fn, name, arguments, ctx)
        return _invoke(fn, name, arguments, ctx)


def _bind(
    fn: Callable[..., Any], name: str, arguments: dict[str, Any], ctx: Any
) -> inspect.BoundArguments:
    """Bind the model's ``arguments``, adding the run context the model cannot send.

    ``eval_str`` matches the derivation at registration time, so the annotation
    seen here is the same object that shaped the tool's schema rather than the
    string a module's ``from __future__ import annotations`` leaves behind.
    """
    sig = inspect.signature(fn, eval_str=True)
    context_parameter = run_context_parameter(sig)
    if context_parameter is not None:
        if ctx is None:
            raise TypeError(
                f"tool {name!r} takes a RunContext as its {context_parameter!r} "
                "parameter; pass ctx= to dispatch it directly."
            )
        # The caller's context last, so a model that invented a ``ctx``
        # argument cannot shadow the real one with a value of its own.
        arguments = {**arguments, context_parameter: ctx}
    bound = sig.bind(**arguments)
    bound.apply_defaults()
    return bound


def _invoke(fn: Callable[..., Any], name: str, arguments: dict[str, Any], ctx: Any) -> Any:
    bound = _bind(fn, name, arguments, ctx)
    return fn(*bound.args, **bound.kwargs)


async def _ainvoke(fn: Callable[..., Any], name: str, arguments: dict[str, Any], ctx: Any) -> Any:
    bound = _bind(fn, name, arguments, ctx)
    return await fn(*bound.args, **bound.kwargs)


__all__ = ["ToolRegistry"]
