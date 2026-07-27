# Integrations

Two optional bridges expose an existing DRF surface as agent tools, so you don't
re-declare your API as `@tool` functions. Both are lazily imported — the base
install stays slim and neither dependency is pulled unless configured.

Both make the agent act as the **logged-in user**: the request is carried into
every call, so your existing permission checks apply exactly as they would over
HTTP.

## Which one?

| | `[spec-tools]` | `[drf-mcp]` |
| --- | --- | --- |
| Path | `drf-services` spec → tool, in process | `drf-mcp-server` registry → toolset, in process |
| Needs an MCP server | **No** | Yes (but no network hop) |
| Use when | The agent is the consumer, and you have specs | You already run an MCP server and want the same tools in the agent |

If you have `ServiceSpec` / `SelectorSpec` objects and no MCP server, take
`[spec-tools]`. If you already run an MCP server for external clients, take
`[drf-mcp]` and get the identical tool surface without standing up a second one.

## `[spec-tools]` — specs as tools

```bash
pip install django-pydantic-agent[spec-tools]
```

`build_spec_capability(specs, request, exclude_names=…)` returns a
`SpecCapability` over the specs, bound to `request.user`. Each call runs through
drf-services' transport-neutral surface and enforces the spec's own
`permission_classes`.

Choosing the capability over a bare toolset is deliberate, though the reason
changed in PAI 0.6.0: the tool-set conventions (list-tool `page` / `limit` /
`order`, and the error contract) now live on `SpecToolset.get_instructions()`,
so they reach the model either way and the capability *delegates* rather than
re-emitting them. What wrapping buys is the capability seam itself —
`defer_loading`, and a uniform place to compose spec tools alongside audit and
guard.

### Declaring specs once

`specs` takes either a `name -> spec` mapping or a **spec registry** —
drf-services 0.27's `SpecRegistry`, the single declaration site for a project
exposing the same specs over more than one transport:

```python
from django_pydantic_agent.integrations.build_spec_capability import build_spec_capability

capability = build_spec_capability(registry, request)
```

The registry is matched **structurally**, through a `SpecSource` Protocol
declaring only `specs() -> dict`. This package names no drf-services type and
depends on `pydantic-ai-slim` alone; drf-services arrives only with the extra.

If you are writing a transport, normalise with `resolve_spec_mapping()` before
you touch the argument. Iterating a registry yields `RegisteredSpec` **records,
not names** — so a transport that reserves tool names by iterating the raw
argument fills its collision set with dataclasses and silently stops detecting
duplicates:

```python
from django_pydantic_agent.integrations.resolve_spec_mapping import resolve_spec_mapping

specs = resolve_spec_mapping(source)   # mapping or registry -> mapping
seen.update(specs)                     # names, as intended
```

## `[drf-mcp]` — an MCP registry as a toolset

```bash
pip install django-pydantic-agent[drf-mcp]
```

`DRFMCPToolset` bridges a `drf-mcp-server` registry through drf-mcp's public
in-process surface (`list_tools` / `acall_tool`), so its validation and
permission checks apply as they would over HTTP — without the network hop and
without reaching into handler internals.

Tool schemas come from drf-mcp's own `tools/list` rather than being re-derived
locally, so the bridge advertises the **same** merged `inputSchema` the HTTP
transport would — including a selector tool's filter / ordering / pagination
arguments and its `additionalProperties` policy, not just the input serializer's
fields.

### Error semantics

The bridge follows MCP's protocol-vs-tool boundary, which decides whether the
model gets to recover:

- malformed argument shape (JSON-RPC `-32602`) and tool-level
  `validation_error` results → `ModelRetry`, so the model retries with the field
  errors instead of the run dying;
- other tool-level failures (`service_error` / `not_found`) → returned as the
  tool's content, model-readable;
- genuine protocol faults (unknown tool, auth, rate limits) → a hard
  `RuntimeError` that aborts the run.

Bridged tools also carry destructiveness into the [tool guard](policy.md): the
bridge maps each tool's `readOnlyHint` annotation onto `DESTRUCTIVE_METADATA_KEY`.

## Name collisions

Both bridges take `exclude_names`, and precedence is fixed: the `@tool` registry
wins, then drf-mcp, then spec tools. A duplicate that slipped through would make
pydantic-ai raise `UserError` at run time, so the excluded set is threaded
through composition instead.

Full signatures in the [integrations reference](reference/integrations.md).
