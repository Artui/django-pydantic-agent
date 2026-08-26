from __future__ import annotations

from typing import Any

from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.constants import DESTRUCTIVE_METADATA_KEY
from django_pydantic_agent.policy.guard.tool_guard import ToolGuard
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_pydantic_agent.registry.build_input_schema import build_input_schema
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg, destructive=True)
    def delete_thing(target: str) -> str:
        """Delete a thing."""
        return f"deleted {target}"

    @tool(reg)
    def read_thing(target: str) -> str:
        """Read a thing (safe)."""
        return f"read {target}"

    return reg


def _def(
    name: str, *, kind: str = "function", metadata: dict[str, Any] | None = None
) -> ToolDefinition:
    return ToolDefinition(
        name=name, parameters_json_schema={"type": "object"}, kind=kind, metadata=metadata
    )


async def _prepare(guard: ToolGuard, defs: list[ToolDefinition]) -> dict[str, str]:
    # ``prepare_tools`` ignores ``ctx``; pass ``None`` (tests aren't type-checked).
    prepared = await guard.prepare_tools(None, defs)  # type: ignore[arg-type]
    return {d.name: d.kind for d in prepared}


async def test_flips_destructive_registry_tool_to_unapproved() -> None:
    guard = ToolGuard(_registry(), config=ToolGuardConfig(enabled=True))
    kinds = await _prepare(guard, [_def("delete_thing"), _def("read_thing")])
    assert kinds["delete_thing"] == "unapproved"
    assert kinds["read_thing"] == "function"


async def test_flips_metadata_marked_tool() -> None:
    # A drf-mcp bridged mutating tool carries destructiveness in metadata, not in
    # the registry — the guard must gate it just the same.
    guard = ToolGuard(ToolRegistry(), config=ToolGuardConfig(enabled=True))
    kinds = await _prepare(
        guard,
        [
            _def("mcp_delete", metadata={DESTRUCTIVE_METADATA_KEY: True}),
            _def("mcp_list", metadata={DESTRUCTIVE_METADATA_KEY: False}),
            _def("mcp_read"),
        ],
    )
    assert kinds["mcp_delete"] == "unapproved"
    assert kinds["mcp_list"] == "function"
    assert kinds["mcp_read"] == "function"


async def test_exempt_wins_over_destructive() -> None:
    guard = ToolGuard(
        _registry(),
        config=ToolGuardConfig(enabled=True, exempt=frozenset({"delete_thing"})),
    )
    kinds = await _prepare(guard, [_def("delete_thing")])
    assert kinds["delete_thing"] == "function"


async def test_require_approval_forces_a_non_destructive_tool() -> None:
    guard = ToolGuard(
        _registry(),
        config=ToolGuardConfig(enabled=True, require_approval=frozenset({"read_thing"})),
    )
    kinds = await _prepare(guard, [_def("read_thing")])
    assert kinds["read_thing"] == "unapproved"


async def test_exempt_wins_over_require_approval() -> None:
    guard = ToolGuard(
        ToolRegistry(),
        config=ToolGuardConfig(
            enabled=True,
            exempt=frozenset({"foo"}),
            require_approval=frozenset({"foo"}),
        ),
    )
    kinds = await _prepare(guard, [_def("foo")])
    assert kinds["foo"] == "function"


async def test_external_and_output_tools_are_left_alone() -> None:
    # An external (frontend) tool is already gated client-side; an output tool is
    # not executed. Only ``function`` tools are flipped, even when destructive.
    guard = ToolGuard(
        _registry(),
        config=ToolGuardConfig(enabled=True, require_approval=frozenset({"ext", "out"})),
    )
    kinds = await _prepare(
        guard,
        [
            _def("delete_thing", kind="external"),
            _def("ext", kind="external"),
            _def("out", kind="output"),
        ],
    )
    assert kinds["delete_thing"] == "external"
    assert kinds["ext"] == "external"
    assert kinds["out"] == "output"


class TestEveryDestructivenessSignal:
    """The docstring promises one hook covers every tool the agent sees wherever
    it came from. Each test below is a signal that reached the guard as a
    ``function`` tool and ran unconfirmed."""

    async def test_flips_a_tool_whose_mcp_annotation_says_it_mutates(self) -> None:
        """A toolset that speaks MCP's own vocabulary nests ``readOnlyHint``
        under ``annotations`` instead of stamping the metadata key.

        The same spec was therefore gated when it arrived over the drf-mcp
        bridge (which maps the hint onto the key) and ungated when the identical
        spec was attached in process.
        """
        guard = ToolGuard(ToolRegistry(), config=ToolGuardConfig(enabled=True))
        kinds = await _prepare(
            guard,
            [
                _def("create_order", metadata={"annotations": {"readOnlyHint": False}}),
                _def("list_orders", metadata={"annotations": {"readOnlyHint": True}}),
                _def("unhinted", metadata={"annotations": {}}),
                _def("not_a_mapping", metadata={"annotations": "nonsense"}),
            ],
        )
        assert kinds["create_order"] == "unapproved"
        assert kinds["list_orders"] == "function"
        assert kinds["unhinted"] == "function"
        assert kinds["not_a_mapping"] == "function"

    async def test_flips_a_tool_whose_schema_carries_the_destructive_stamp(self) -> None:
        """``build_input_schema`` is a public export, so a project can derive a
        schema outside the registration flow and attach the tool through
        ``toolsets=``. Its ``x-destructive`` stamp had no server-side reader at
        all: the gate saw a plain ``function`` tool and let it run."""

        def purge(target: str) -> str:
            """Purge a thing."""
            return target

        guard = ToolGuard(ToolRegistry(), config=ToolGuardConfig(enabled=True))
        kinds = await _prepare(
            guard,
            [
                ToolDefinition(
                    name="purge",
                    parameters_json_schema=build_input_schema(purge, destructive=True),
                ),
                ToolDefinition(
                    name="peek",
                    parameters_json_schema=build_input_schema(purge),
                ),
            ],
        )
        assert kinds["purge"] == "unapproved"
        assert kinds["peek"] == "function"

    async def test_exempt_still_wins_over_every_signal(self) -> None:
        guard = ToolGuard(
            ToolRegistry(),
            config=ToolGuardConfig(enabled=True, exempt=frozenset({"create_order"})),
        )
        kinds = await _prepare(
            guard,
            [_def("create_order", metadata={"annotations": {"readOnlyHint": False}})],
        )
        assert kinds["create_order"] == "function"


async def test_gates_the_same_service_spec_a_spec_toolset_exposes() -> None:
    """The reproduction, end to end: a mutating spec attached in process.

    ``SpecToolset`` builds its tool definitions from the spec kind — a
    ``ServiceSpec`` mutates, a ``SelectorSpec`` reads — and says so with MCP's
    ``readOnlyHint``. Moving a spec off the drf-mcp bridge and into the same
    process is presented as a transport swap, so the gate has to survive it.
    """
    from rest_framework.permissions import AllowAny
    from rest_framework_pydantic_ai import SpecToolset
    from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec

    def create_order(user: Any) -> dict[str, Any]:
        """Create an order."""
        return {"ok": True}

    def list_orders(user: Any) -> list[Any]:
        """List orders."""
        return []

    toolset = SpecToolset(
        {
            "create_order": ServiceSpec(
                service=create_order, atomic=False, permission_classes=[AllowAny]
            ),
            "list_orders": SelectorSpec(
                kind=SelectorKind.LIST, selector=list_orders, permission_classes=[AllowAny]
            ),
        }
    )
    # ``get_tools`` ignores its ``RunContext``; there is no run here.
    tool_defs = [t.tool_def for t in (await toolset.get_tools(None)).values()]  # type: ignore[arg-type]

    guard = ToolGuard(ToolRegistry(), config=ToolGuardConfig(enabled=True))
    kinds = await _prepare(guard, tool_defs)

    assert kinds["create_order"] == "unapproved"
    assert kinds["list_orders"] == "function"
