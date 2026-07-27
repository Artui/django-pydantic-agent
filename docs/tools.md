# Tools

## Registering one

A tool is a plain typed callable registered on a `ToolRegistry`:

```python
from django_pydantic_agent import ToolCategory, ToolRegistry, tool

registry = ToolRegistry()


@tool(registry, category=ToolCategory.UI_READ)
def find_order(order_id: int) -> dict:
    """Look up an order by id."""
    ...
```

The name defaults to the function's name, and the description to the first
paragraph of its docstring; both can be overridden. Registering a name twice
raises `ValueError` rather than shadowing.

`ToolRegistry` state lives on the **instance** — a transport holds one, and
tests build a fresh one per scenario. There is no global registry.

## Tools must be typed

The registry derives each tool's JSON Schema from the signature at registration
time, so **every parameter and the return need a concrete annotation**. There is
no `**kwargs: Any` escape hatch: an untyped parameter has no schema, and the
model would be guessing.

Sync and async callables both work; the registry dispatches either.

## Metadata, and where it surfaces

`@tool` takes four pieces of metadata beyond name and description. All of them
end up as JSON-Schema extension keys, because a schema is the one channel that
reaches a client without inventing a side channel:

| Argument | Schema key | What it is for |
| --- | --- | --- |
| `destructive=True` | `x-destructive` | This tool mutates. AG-UI has no native destructive-tool concept, so it is stamped at the schema root and read client-side to gate execution behind a confirmation. |
| `category=` | `x-category` | Coarse grouping (`ToolCategory`) so a frontend can group or filter, or a system prompt can reason about capability classes. |
| `confirm="Activate this project?"` | `x-confirm` | The confirmation prompt shown instead of a generic "Run *tool*?". |
| `summary="Query orders"` | `x-summary` | A short display label shown on a tool-call card instead of the raw tool name. |

**`category` does not gate anything.** It is advisory metadata for grouping and
policy; `destructive` is the flag that drives the approval gate.

There is a fifth key that is *not* a schema stamp: `DESTRUCTIVE_METADATA_KEY`
(`"django_pydantic_agent.destructive"`) rides pydantic-ai's `ToolDefinition`
metadata rather than the schema, because it has a different audience. The `x-*`
keys reach the *client*; this one is read **server-side** at `prepare_tools`
time by the [tool guard](policy.md), which needs destructiveness for tools whose
flag doesn't come from the `@tool` registry at all — a bridged drf-mcp tool,
whose `readOnlyHint` annotation the bridge maps onto this key.

## The tool catalog

Server-side tools execute server-side, so their JSON Schema never reaches the
browser — which means a web component can't read an `x-summary` off it. That is
what `build_tool_catalog` is for: a list of `{"name", "summary", "description"?}`
entries a frontend fetches to label tool-call cards.

`summary` is always present, resolved through a fallback chain — for registry
tools, `@tool(summary=…)` then a prettified name; for drf-mcp tools,
`display_name` then `title` then a prettified name.

```python
from django_pydantic_agent import build_tool_catalog

catalog = build_tool_catalog(registry, drf_mcp_server=server, service_specs=specs)
```

## Deriving a schema on its own

`build_input_schema` is the same derivation the registry runs, exposed for
projects that want a tool's schema outside the registration flow:

```python
from django_pydantic_agent import build_input_schema

schema = build_input_schema(find_order, destructive=False, category=ToolCategory.UI_READ)
```

See the [registry reference](reference/registry.md) for the full signatures.
