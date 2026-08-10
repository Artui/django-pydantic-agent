# Integrations

Three optional extras, all lazily imported — the base install stays slim and
none of these dependencies is pulled unless configured.

Two of them are **bridges**: `[spec-tools]` and `[drf-mcp]` expose an existing
DRF surface as agent tools, so you don't re-declare your API as `@tool`
functions. Both make the agent act as the **logged-in user** — the request is
carried into every call, so your existing permission checks apply exactly as they
would over HTTP.

The third, `[harness]`, is a different shape: it brings in ready-made
**capabilities** (compaction, agent skills, step persistence and more) that plug
into the same `AgentConfig.capabilities` seam this package's own audit and
tool-guard capabilities use.

## Which bridge?

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
`ordering`, and the error contract) now live on `SpecToolset.get_instructions()`,
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

specs = resolve_spec_mapping(source)  # mapping or registry -> mapping
seen.update(specs)  # names, as intended
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

## `[harness]` — upstream capabilities

```bash
pip install django-pydantic-agent[harness]
```

The other two extras bridge *your* API into the agent. This one is different: it
pulls in [`pydantic-ai-harness`](https://github.com/pydantic/pydantic-ai-harness),
a library of ready-made **capabilities** — the same seam this package's own audit
and tool-guard capabilities use. Nothing here wraps or re-implements them; you
construct one and hand it to `AgentConfig`:

```python
from pydantic_ai_harness.compaction import SlidingWindow

config = AgentConfig(model=..., capabilities=[SlidingWindow(max_messages=80)])
agent = build_agent(registry, config)
```

That is the whole integration. `capabilities` takes live instances (never dotted
paths), so a harness capability, a first-party one, and your own all compose the
same way, and pydantic-ai orders them by their own `get_ordering()`.

!!! warning "Version-sensitive"
    `pydantic-ai-harness` is 0.x and its minors may break. This package pins
    `>=0.12,<0.13`; check the changelog before widening it. The 0.7 → 0.12 jump
    changed the `StepStore` protocol, which is why `DefaultStepStore` grew an
    `include_interrupted` argument and a `state` column.

### Long runs: compaction

A tool-heavy run grows its message history until it crowds the context window.
The `compaction` module trims it, and the choice is mostly about cost:

| Strategy | Cost | What it does |
| --- | --- | --- |
| `SlidingWindow(max_messages=80, keep_messages=40)` | free | Drops the oldest messages once a threshold is crossed, preserving tool-call / tool-return pairs. No model calls. |
| `ClearToolResults(max_messages=60, keep_pairs=3)` | free | Keeps the conversation shape but blanks old tool *results*, which are usually the bulk of the tokens. |
| `SummarizingCompaction(max_messages=60, keep_messages=20)` | **a model call** | Replaces the trimmed span with an LLM-written summary, so older context survives in compressed form. |
| `TieredCompaction(tiers=[...], target_tokens=…)` | varies | Applies cheaper strategies first, escalating only if still over target. |

Start with `SlidingWindow` — it is free and transparent. Reach for
`SummarizingCompaction` only when losing the old turns outright actually hurts,
since it spends a model call on every compaction.

Trimming happens inside `before_model_request` and is deliberately invisible to
the rest of the run: **nothing is emitted when a compaction fires**. A transport
that wants to tell the user "earlier turns were condensed" has to observe it
itself — `CompactionStrategy` is a one-method protocol
(`compact(messages, ctx) -> messages`), so a thin wrapper that compares its input
to its output is the seam for that.

### Progressive disclosure: agent skills

A *skill* is a folder with a `SKILL.md` — a name, a description, and a body of
instructions. `Skills` discovers them and exposes each as a **deferred**
capability: the model sees only the name and description up front, and the body
loads into context if it selects that skill. A dozen skills therefore cost a
dozen one-line descriptions, not a dozen instruction blocks.

```python
from pydantic_ai_harness.skills import Skills

config = AgentConfig(
    model=...,
    capabilities=[Skills("/srv/app/skills")],
)
```

`directories` takes one path or several. Discovery scans *immediate* child
directories for a `SKILL.md`, so `/srv/app/skills/summarise/SKILL.md` registers a
skill named `summarise` (the frontmatter `name` wins over the directory name when
both are present).

`include` / `exclude` select by exact skill name — and are **validated against
what was actually discovered**, so `exclude={"draft-only"}` raises `ValueError`
when no such skill exists rather than silently excluding nothing. That is the
behaviour you want (a typo'd name fails loudly), but it does mean a selection
list and a skills directory have to be kept in step.

!!! note "Instructions only, in v1"
    Upstream loads the `SKILL.md` body and nothing else — bundled scripts and
    resources are deferred to a future sandbox integration. A skill that assumes
    it can execute its own files will not work yet.

Each skill becomes one capability whose `id` is the skill name, which is what a
client needs to show "using skill X". Enumerate them with `skills.apply(visitor)`.

### Also available

`step_persistence` (used by [`DefaultStepStore`](storage.md)), `subagents`,
`code_mode`, `overflowing_tool_output`, `guardrails`, `filesystem`, `shell` and
`ManagedPrompt` all compose the same way. None of them need support from this
package — if it is a pydantic-ai capability, `capabilities=[...]` takes it.

## Name collisions

Both bridges take `exclude_names`, and precedence is fixed: the `@tool` registry
wins, then drf-mcp, then spec tools. A duplicate that slipped through would make
pydantic-ai raise `UserError` at run time, so the excluded set is threaded
through composition instead.

Full signatures in the [integrations reference](reference/integrations.md).
