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

It rides the `wrap_tool_execute` lifecycle hook, which is the point: it times
and records **every tool the agent runs**, not just the ones in the registry —
drf-mcp and spec-toolset bridges, attachment tools, everything. A per-tool
wrapper would miss the composed toolsets.

`NullAuditLogger` is the no-op default; `build_agent` skips composition entirely
when the logger is null, so "auditing off" costs nothing.

### What lands in a record

`AuditEvent` carries `tool_name`, `arguments_repr`, `duration_ms`, `success`,
and optional `error` / `result_size`.

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

Destructiveness is unified from three sources, so one hook covers every tool
regardless of origin:

- **Registry tools** — `@tool(destructive=True)`. The flag lives on the spec and
  never reaches pydantic-ai as a bare callable, so the guard reads it from the
  registry directly at construction.
- **drf-mcp bridged tools** — the bridge maps each tool's `readOnlyHint`
  annotation onto `DESTRUCTIVE_METADATA_KEY`, which the guard reads from
  pydantic-ai's tool metadata.
- **`require_approval`** — an explicit opt-in for anything the first two miss.

That is why `DESTRUCTIVE_METADATA_KEY` rides tool *metadata* rather than the
`x-destructive` JSON-Schema stamp: the schema keys are for the client, this one
is read server-side at `prepare_tools` time.

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

Two things worth knowing:

- **It hangs off `on_tool_execute_error`, not a `try` around the handler.**
  Pydantic-AI does not route control-flow exceptions to that hook —
  `SkipToolExecution`, `CallDeferred`, `ApprovalRequired`, the `ModelRetry`
  retry signal, or an explicit `ToolFailed`. So the approval gate above and the
  model's retry budget both pass through untouched. A hand-written
  `except Exception` would have caught `ApprovalRequired` and silently disabled
  the gate.
- **It spends no retry budget**, because `ToolFailed` deliberately doesn't.
  A model can call a persistently broken tool again; bound that with run-level
  `UsageLimits` rather than expecting this to stop it.

## Ordering

No capability here needs positioning. Each declares its place via
`get_ordering()` — audit outermost, the guard orthogonal — and pydantic-ai's
`CombinedCapability` topologically sorts them. The failure policy needs no
constraint at all: it rides `on_tool_execute_error` while audit rides
`wrap_tool_execute`, so the failure is recorded and then converted whichever
way the list is sorted. Append your own capabilities in any order.

Full signatures in the [policy reference](reference/policy.md).
