# Policy

Two orthogonal concerns ride the Pydantic-AI capability seam: an **audit trail**
over every tool call, and an **approval gate** for destructive ones. Both are
off by default.

## Audit

Set an `audit_logger` on the config and `build_agent` composes an
`AuditCapability`:

```python
from django_pydantic_agent import AgentConfig, LoggingAuditLogger, build_agent

agent = build_agent(registry, AgentConfig(model=..., audit_logger=LoggingAuditLogger()))
```

It hooks pydantic-ai's tool execution itself, which is the point: it times and
records **every tool the agent runs**, not just the ones in the registry —
drf-mcp and spec-toolset bridges, attachment tools, everything. A per-tool
wrapper would miss the composed toolsets.

`NullAuditLogger` is the no-op default; `build_agent` skips composition entirely
when the logger is null, so "auditing off" costs nothing.

### What lands in a record

`AuditEvent` carries `tool_name`, `arguments_repr`, `duration_ms`, `success`,
and optional `error` / `result_size`.

**There is one record per tool execution, and it describes the tool's own
execution**, whatever other capabilities are composed around it and on every
supported pydantic-ai release:

- `arguments_repr` holds the arguments the tool received, after every other
  capability's `before_tool_execute` has rewritten them.
- `success` and `error` describe what the tool did. `error` reads
  `Type: message` for the exception the tool raised, before anything converts
  it or recovers from it.
- `result_size` is the length of the tool's own result, before any
  `after_tool_execute` rewrites it.
- `duration_ms` spans the tool alone: from after every other capability's
  `before_tool_execute` to before any `on_tool_execute_error` or
  `after_tool_execute`. Time spent in other capabilities' hooks is not in it.

**A failure is recorded as the failure, whatever happens to it next.** The
[failure policy](#what-a-raising-tool-costs) turns a tool's exception into a
`ToolFailed` for the model, redacted unless `include_detail` is set; the record
still names the exception the tool raised, because this is the operator's copy.
A capability whose `on_tool_execute_error` returns a value in the exception's
place has decided what the run does next, not what the tool did, so that
failure is recorded as a failure too.

Two exceptions never reach any capability's error hook, and are recorded as
they propagate. A `ToolFailed` that **a tool raises itself** is converted by
pydantic-ai into a `ToolFailedError` carrying the same message, so
`raise ToolFailed("model copy") from e` is recorded as
`ToolFailedError: model copy`, and the policy is never handed it. The drf-mcp
bridge's refusals arrive the same way. A `ModelRetry` is recorded as
`ToolRetryError: <message>`, and the retried call gets a record of its own.

**A call that never reaches the tool produces no record.** A
`before_tool_execute` that stops the call is not an execution, and neither is a
destructive call the [gate](#the-destructive-tool-gate) holds for approval.
pydantic-ai-harness's guardrails and tool-call judge stop a call that way, with
`SkipToolExecution`. The approved call, when it runs, is recorded like any
other. A record of what was refused belongs to whatever refused it.

Audit never changes what the run sees. Its error hook re-raises what it is
handed, and a sink that raises is caught and logged to the
`django_pydantic_agent.audit` logger, costing the record rather than the run.

Arguments are stored **as a string** (typically JSON-encoded), deliberately: it
keeps records cheap to serialize and discourages retaining raw sensitive values.

`organization_id` and `target_type` exist on the shape but are `None` at this
layer — tool arguments are domain-opaque here. A custom `AuditLogger` fills them
from its own tenancy and domain model.

### The run-level record

One record isn't a tool call: when a client disconnects mid-run, the transport
records `tool_name="agent.run"` with `success=False` and an `error` starting
`"cancelled:"`. That keeps cancelled runs distinguishable in an audit sink
without widening the `AuditLogger` protocol to carry a second event shape.

Write your own sink by implementing `AuditLogger` — a database table, a
log-shipping pipeline, whatever your compliance story needs.

## The destructive-tool gate

Pydantic-AI supplies the *mechanism*: a tool whose definition is
`kind="unapproved"` defers to an interrupt the client approves or denies.
`ToolGuard` supplies the *policy* — at `prepare_tools` time it flips a plain
`function` tool to `unapproved` when the tool is destructive, so **server-side**
tools get the same confirmation gate a web component already applies to
client-registered ones.

```python
from django_pydantic_agent import AgentConfig, ToolGuardConfig

config = AgentConfig(
    model=...,
    tool_guard=ToolGuardConfig(
        enabled=True,
        exempt=frozenset({"send_receipt"}),
        require_approval=frozenset({"export_report"}),
    ),
)
```

**Off by default.** `ToolGuardConfig.enabled` is `False`, so the gate never
surprises a project that hasn't opted in.

### How a tool is judged destructive

When enabled, a tool is gated unless its name is in `exempt`, and it is gated
when **either** it is destructive **or** its name is in `require_approval`.
`exempt` wins over `require_approval`.

Destructiveness is unified from every vocabulary a toolset declares it in, so
one hook covers every tool regardless of origin:

- **Registry tools** — `@tool(destructive=True)`. The flag lives on the spec and
  never reaches pydantic-ai as a bare callable, so the guard reads it from the
  registry directly at construction.
- **drf-mcp bridged tools** — the bridge maps each tool's `readOnlyHint`
  annotation onto `DESTRUCTIVE_METADATA_KEY`, which the guard reads from
  pydantic-ai's tool metadata.
- **MCP tool annotations** — `metadata["annotations"]["readOnlyHint"] is False`,
  which is how a toolset speaking MCP's own vocabulary says it mutates. A
  drf-services `ServiceSpec` exposed through `SpecToolset` is the case that
  matters: without this the *same* spec was gated over the drf-mcp bridge and
  ungated attached in process, so a transport swap removed the gate silently.
- **The `x-destructive` schema stamp** at the root of `parameters_json_schema`,
  which is what `build_input_schema` writes. A project deriving a schema with
  that helper and attaching the tool through `toolsets=` gets the gate from it.
- **`require_approval`** — an explicit opt-in for anything the rest miss.

A hint has to *say* the tool mutates. A missing `readOnlyHint`, an absent stamp,
or metadata of some other shape entirely leaves the tool alone — silence is not
a claim, and `require_approval` is the answer for a tool whose source declares
nothing.

`DESTRUCTIVE_METADATA_KEY` still rides tool *metadata* rather than only the
schema, because metadata is the channel a bridge controls and the schema is the
tool author's; the guard reads both, and a client reads the schema alone.

### What is gated, and what is not

Worth stating plainly, because a system prompt that promises a confirmation the
server does not perform is worse than no promise at all:

- With **no `tool_guard`** — the stock configuration — every server-side tool
  runs the moment the model calls it. There is no interrupt and no card.
- With `tool_guard` **enabled**, a tool is gated when the registry, its
  metadata, its schema or `require_approval` says so, and not otherwise.
- The browser's own confirmation card is a **separate** path: it reads a
  *client-registered* tool's `parameters`, so it never sees a tool that executes
  server-side. Neither path substitutes for the other.

## What a raising tool costs

A tool that raises used to end the run. The transport emitted `RUN_ERROR`, the
turn stopped, and everything the model had already produced went with it —
along with the results of every other tool in the same round. One broken
integration cost the whole answer.

`ToolFailurePolicy` changes what the failure stops, and nothing else. The call
comes back to the model marked failed, naming the tool, and the run carries on:

```python
config = AgentConfig(model="openai:gpt-4o")  # on by default
```

Turn it off to restore the old behaviour, or opt into detail:

```python
from django_pydantic_agent import ToolFailureConfig

AgentConfig(model=..., tool_failure=ToolFailureConfig(enabled=False))
AgentConfig(model=..., tool_failure=ToolFailureConfig(include_detail=True))
```

**`include_detail` is off by default, and the split is deliberate.** Whether the
run survives is a reliability question; whether the exception's text reaches the
model is a disclosure one. A traceback message can carry a query, a path or a
credential, and anything handed to the model is also handed to whatever renders
the transcript. The operator's copy is never redacted — the full exception goes
to the audit logger and to the `django_pydantic_agent.failure` logger either way.

Three things worth knowing:

- **It hangs off `on_tool_execute_error`, not a `try` around the handler.**
  Pydantic-AI does not route control-flow exceptions to that hook —
  `SkipToolExecution`, `CallDeferred`, `ApprovalRequired`, the `ModelRetry`
  retry signal, or an explicit `ToolFailed`. So the approval gate above and the
  model's retry budget both pass through untouched. A hand-written
  `except Exception` would have caught `ApprovalRequired` and silently disabled
  the gate.
- **It converts last.** It is pinned outermost and `build_agent` places it
  first, so its `on_tool_execute_error` runs after every other capability's
  (see [ordering](#ordering)). Every other error hook is handed the exception
  the tool raised, never the policy's redacted copy: a step recorder such as
  pydantic-ai-harness's `StepPersistence` logs the real failure, and a
  capability that recovers by returning a value answers before anything is
  converted. An earlier error hook that raises `ModelRetry` or `ToolFailed` in
  the exception's place has answered for the model, so that passes through
  unconverted.
- **It spends no retry budget**, because `ToolFailed` deliberately doesn't.
  A model can call a persistently broken tool again; bound that with run-level
  `UsageLimits` rather than expecting this to stop it.

### A denial is not a tool failure

An authorization refusal passes through untouched and ends the run, exactly as
it would with the policy off. `django.core.exceptions.PermissionDenied` and —
when DRF is installed — `rest_framework.exceptions.PermissionDenied` are both in
the default set; a spec tool's permission check raises the latter.

Converting one would leave the run alive with the model free to try the next
row, and a converted denial stays distinguishable from a missing row. A sweep
over ids therefore turns the permission boundary into an existence oracle over
rows the acting user cannot read — inside a single turn, spending no retry
budget, bounded by nothing `build_agent` sets.

That sentence used to read "distinguishable from a `{"error": "not found"}`
one", which described how `djangorestframework-pydantic-ai` reported a missing
row before it began raising `ToolFailed` for one. The shape changed and the
argument did not, but the reason is worth stating because the change looks like
it closed the hole and did not. A missing row and a converted denial now share
an `outcome` — both are `failed` — so the two are no longer told apart by the
result's *shape*. They are still told apart by its *words*: one says the row is
not there and the other says the tool failed. An oracle needs one bit, and a
message is a place to find one.

The set is a project decision:

```python
ToolFailureConfig(reraise=())  # convert everything (the old behaviour)
ToolFailureConfig(reraise=(PermissionDenied, LookupError))
```

## Ordering

Each capability here declares its own place through `get_ordering()`, and
pydantic-ai's `CombinedCapability` sorts them topologically, with list order
breaking ties within a tier. Add your own to `AgentConfig.capabilities` in any
order; `build_agent` positions its own around them.

- **Audit is innermost**, the capability closest to the tool: its
  `before_tool_execute` runs last, and its `on_tool_execute_error` and
  `after_tool_execute` run first. `build_agent` appends it after
  `config.capabilities`, which keeps it inside other innermost capabilities
  too, such as pydantic-ai-harness's guardrail and tool-call judge, so their
  vetoes never reach it.
- **The failure policy is outermost**, and `build_agent` places it first, which
  keeps it outside other outermost capabilities. pydantic-ai runs error hooks
  innermost first, so the policy's runs last.
- **The guard is orthogonal.** It touches only `prepare_tools`, before any call
  is made.

Composing these by hand rather than through `build_agent`, keep the same
places: `AuditCapability` after any other innermost capability, and
`ToolFailurePolicy` before any other outermost one. Ahead of an innermost
guardrail, audit would run its hooks before the guardrail's veto, and on
pydantic-ai 2.54 and later would record the vetoed call as a failure.

### Why audit needs all four hooks

pydantic-ai 2.54 moved every capability's `before_tool_execute`,
`on_tool_execute_error` and `after_tool_execute` inside the
`wrap_tool_execute` chain; earlier releases ran them around it. A wrapper that
recorded only what it saw would therefore describe a different thing on each
side of 2.54. From 2.54 it would record a failure another capability recovers
from as a success, a vetoed call as a failure, the arguments before a rewrite
and the result after one, and other capabilities' hooks in the duration.

Audit records the same thing on both because it observes the tool from all four
hooks. Its `before_tool_execute` captures the arguments and the start, its
`on_tool_execute_error` the tool's exception, and its `after_tool_execute` the
tool's result. Its wrapper is the only one that writes the record, once per
execution, as it exits, preferring what the hooks captured over what it saw
itself. Before 2.54 the innermost wrapper encloses the tool alone, so what it
sees is already the tool's outcome; from 2.54 it encloses every hook, and the
captures are what keep the record the tool's. Each call's captures belong to
that call, so parallel calls in one run and concurrent runs of one agent never
read each other's.

Full signatures in the [policy reference](reference/policy.md).
