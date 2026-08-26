from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import RunContext

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.constants import ToolCategory
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from django_pydantic_agent.registry.types.tool_spec import ToolSpec


def make_spec(name: str = "noop") -> ToolSpec:
    def fn(x: int = 1) -> int:
        return x + 1

    return ToolSpec(name=name, fn=fn, description="d")


def test_register_and_dispatch() -> None:
    reg = ToolRegistry()
    binding = reg.register(make_spec())
    assert "noop" in reg
    assert len(reg) == 1
    assert list(reg) == [binding]
    assert reg.get("noop") is binding
    assert binding.input_schema["x-category"] == ToolCategory.OTHER.value
    assert reg.call("noop", {"x": 5}) == 6
    assert reg.call("noop", {}) == 2  # default applies


def test_register_duplicate_name_rejected() -> None:
    reg = ToolRegistry()
    reg.register(make_spec())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(make_spec())


def test_get_missing_raises_keyerror() -> None:
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        reg.get("ghost")


def test_call_unknown_raises_keyerror() -> None:
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.call("ghost", {})


def test_call_async_function_refused_from_sync() -> None:
    async def coro(x: int = 1) -> int:
        return x

    reg = ToolRegistry()
    reg.register(ToolSpec(name="coro", fn=coro, description="d"))
    with pytest.raises(TypeError, match="async"):
        reg.call("coro", {})


async def test_acall_dispatches_sync_and_async() -> None:
    async def coro(x: int) -> int:
        return x * 2

    def sync(x: int) -> int:
        return x + 1

    reg = ToolRegistry()
    reg.register(ToolSpec(name="coro", fn=coro, description="d"))
    reg.register(ToolSpec(name="sync", fn=sync, description="d"))

    assert await reg.acall("coro", {"x": 3}) == 6
    assert await reg.acall("sync", {"x": 3}) == 4


class TestDispatchingAToolThatTakesTheRunContext:
    """``ctx: RunContext[...]`` is the only way a registry tool reaches the
    acting user. Direct dispatch bound the model's arguments alone, so such a
    tool could not be called through the registry at all — ``sig.bind`` raised
    ``TypeError`` for the missing ``ctx``."""

    def _registry(self, seen: list[Any]) -> ToolRegistry:
        reg = ToolRegistry()

        def find_order(ctx: RunContext[AgentDeps], order_id: int) -> str:
            seen.append(ctx)
            return f"order {order_id}"

        async def afind_order(ctx: RunContext[AgentDeps], order_id: int) -> str:
            seen.append(ctx)
            return f"order {order_id}"

        reg.register(ToolSpec(name="find_order", fn=find_order, description="d"))
        reg.register(ToolSpec(name="afind_order", fn=afind_order, description="d"))
        return reg

    def test_the_context_comes_from_the_caller_not_the_model(self) -> None:
        seen: list[Any] = []
        ctx = SimpleNamespace(deps=AgentDeps(user="alice"))

        assert self._registry(seen).call("find_order", {"order_id": 1}, ctx=ctx) == "order 1"
        assert seen == [ctx]

    async def test_the_async_path_binds_it_the_same_way(self) -> None:
        seen: list[Any] = []
        ctx = SimpleNamespace(deps=AgentDeps(user="alice"))

        result = await self._registry(seen).acall("afind_order", {"order_id": 1}, ctx=ctx)

        assert result == "order 1"
        assert seen == [ctx]

    async def test_a_sync_tool_dispatched_through_acall_gets_it_too(self) -> None:
        seen: list[Any] = []
        ctx = SimpleNamespace(deps=AgentDeps(user="alice"))

        assert await self._registry(seen).acall("find_order", {"order_id": 1}, ctx=ctx) == "order 1"

    def test_omitting_the_context_says_so_instead_of_naming_a_missing_argument(self) -> None:
        """Loud rather than ``ctx=None``: a tool that reads the acting user off a
        context it was silently handed as ``None`` fails somewhere else, or does
        not fail at all."""
        with pytest.raises(TypeError, match="RunContext"):
            self._registry([]).call("find_order", {"order_id": 1})

    async def test_the_async_path_refuses_it_the_same_way(self) -> None:
        with pytest.raises(TypeError, match="RunContext"):
            await self._registry([]).acall("afind_order", {"order_id": 1})

    def test_the_callers_context_wins_over_one_the_model_invented(self) -> None:
        """``ctx`` is not in the schema, so a value for it is a value the model
        made up. It must not be what the tool reads the acting user from."""
        seen: list[Any] = []
        ctx = SimpleNamespace(deps=AgentDeps(user="alice"))

        self._registry(seen).call("find_order", {"order_id": 1, "ctx": "spoofed"}, ctx=ctx)

        assert seen == [ctx]

    def test_a_context_passed_to_a_tool_that_wants_none_is_ignored(self) -> None:
        reg = ToolRegistry()
        reg.register(make_spec())

        assert reg.call("noop", {"x": 5}, ctx=SimpleNamespace()) == 6
