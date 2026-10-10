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
execution**, whatever other capabilities sort ahead of audit, which is
everything composed through `AgentConfig.capabilities` unless its own ordering
places it inside audit, and on every supported pydantic-ai release:

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

A capability that **sorts after audit**, such as an innermost one passed to a
single run, is outside that promise. It runs between audit and the tool, so
what it does can reach the record, on every pydantic-ai release and through
any of its hooks, and the record is then not the tool's own.
[A capability that sorts after audit](#a-capability-that-sorts-after-audit)
says when a capability sorts there and gives measured examples of what it
changes.

**A failure is recorded as the failure, whatever a capability ahead of audit
does with it next.** The
[failure policy](#what-a-raising-tool-costs) turns a tool's exception into a
`ToolFailed` for the model, redacted unless `include_detail` is set; the record
still names the exception the tool raised, because this is the operator's copy.
A capability whose `on_tool_execute_error` returns a value in the exception's
place has decided what the run does next, not what the tool did, so that
failure is recorded as a failure too. One that
[sorts after audit](#a-capability-that-sorts-after-audit) is the exception:
a recovery in its error hook is recorded as a success from pydantic-ai 2.54,
and so is one in its wrapper before 2.54, or from 2.54 when the tool raised
`ModelRetry` or `ToolFailed`.

On an ordinary call two exceptions never reach any capability's error hook,
and are recorded as they propagate. A `ToolFailed` that **a tool raises
itself** is converted by pydantic-ai into a `ToolFailedError` carrying the same
message, so `raise ToolFailed("model copy") from e` is recorded as
`ToolFailedError: model copy`, and the policy is never handed it. The drf-mcp
bridge's refusals arrive the same way. A `ModelRetry` is recorded as
`ToolRetryError: <message>`, and the retried call gets a record of its own.
From a code-mode sandbox, whose nested tool manager converts neither, both are
recorded as raised: `ToolFailed: model copy` and `ModelRetry: <message>`.

**Once a tool's retry budget is spent, its `ModelRetry` is recorded
differently.** On an ordinary call pydantic-ai raises `UnexpectedModelBehavior`
in its place, from inside the call, so it does reach the error hooks: the
record reads `UnexpectedModelBehavior: Tool '<name>' exceeded max retries count
of <n>...`, and the [failure policy](#what-a-raising-tool-costs) converts it
into a failed result like any other exception.

**A `ToolFailed` keeps its own message, cause or not**, because whoever raised
it chose that message as the outcome: a tool, or a toolset such as the spec
bridge. Its cause can say less than the message does: a spec tool's timeout
names the limit in the message, while the `asyncio` timeout it is raised from
has no text at all. Neither an exception's cause nor its context is read into
the record. A tool's own `ToolFailed` is recorded as
`"ToolFailedError: <message>"`, or as `"ToolFailed: <message>"` when it is
called from a code-mode sandbox.

**A call that never runs the tool produces no record.** Two things mark one:

- **It stopped before audit's `before_tool_execute`**, with any exception: a
  veto, a `ModelRetry` asking for different arguments, or an error.
- **Its outcome is one pydantic-ai treats as not executed**: a
  `SkipToolExecution` veto, or a `CallDeferred` or `ApprovalRequired`
  deferral, wherever it was raised. A tool can raise either deferral itself,
  and a toolset can raise one for it, as pydantic-ai's
  `FunctionToolset.approval_required()` does.

So neither a call pydantic-ai-harness's guardrails or tool-call judge veto, nor
a destructive call the [gate](#the-destructive-tool-gate) holds for approval,
nor any other call deferred for approval has a record. The approved call is
recorded when it is resumed and runs, like any other. A call deferred to
external execution never runs in this process, so it has no record here: the
record belongs to whatever executes it, as a record of what was refused
belongs to whatever refused it.

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
the transcript. The policy never redacts the operator's copy. Audit records the
exception it is handed in full ([what lands in a record](#what-lands-in-a-record)),
unless a capability [sorts after audit](#a-capability-that-sorts-after-audit) and
changes it, and whenever the policy converts a failure it first logs the
exception, with its traceback, to the `django_pydantic_agent.failure` logger.
That logger hears only about the failures the policy converts: nothing when
another capability recovered the call or answered for the model with its own
`ModelRetry` or `ToolFailed`, when an authorization refusal passes through, or
when the exception never reached the error hooks at all, as a tool's own
`ToolFailed` does not, nor its `ModelRetry` while it has retries left. A
tool's `ModelRetry` with no retries left does reach them, as
`UnexpectedModelBehavior`, so the policy converts it and logs it.

Three things worth knowing:

- **It hangs off `on_tool_execute_error`, not a `try` around the handler.**
  Pydantic-AI does not route control-flow exceptions to that hook —
  `SkipToolExecution`, `CallDeferred`, `ApprovalRequired`, the `ModelRetry`
  retry signal, or an explicit `ToolFailed`. So the approval gate above passes
  through untouched, and so does a `ModelRetry` while the tool has retries
  left. A hand-written `except Exception` would have caught `ApprovalRequired`
  and silently disabled the gate. The policy neither spends nor grants retries,
  but it does change what happens when a tool's own budget runs out: pydantic-ai
  raises `UnexpectedModelBehavior` in the last `ModelRetry`'s place from inside
  the call, which is routed to that hook, so the policy converts it like any
  other failure. The run continues with a failed result where without the
  policy it would end. A capability's `ModelRetry` is different: pydantic-ai
  checks its budget after every error hook has run, outside the policy's reach,
  so it still ends the run when the budget is spent.
- **It converts last.** It is pinned outermost and `build_agent` places it
  first, so its `on_tool_execute_error` runs after every other capability's
  (see [ordering](#ordering)). Every other error hook is handed the exception
  the tool raised, never the policy's redacted copy: a step recorder such as
  pydantic-ai-harness's `StepPersistence` logs the real failure, and a
  capability that recovers by returning a value answers before anything is
  converted. An earlier error hook that raises `ModelRetry` or `ToolFailed` in
  the exception's place has answered for the model, so that passes through
  unconverted. A `ModelRetry` passed through spends the tool's retry budget,
  and once that is spent the run ends with `UnexpectedModelBehavior`, which
  pydantic-ai raises after every error hook has run, so the policy never sees
  it. That is the opposite of a tool's own `ModelRetry`, above.
- **It spends no retry budget**, because `ToolFailed` deliberately doesn't.
  A model can call a persistently broken tool again; bound that with run-level
  `UsageLimits` rather than expecting this to stop it.

### A tool that has spent its retries

A tool that keeps raising `ModelRetry` is told to try again until its retry
budget (`AgentConfig.retries`) is spent.
Without the policy the next `ModelRetry` ends the run with
`UnexpectedModelBehavior`. With it, that exception is converted: the model gets
a failed result, the run continues, audit and the
`django_pydantic_agent.failure` logger record `UnexpectedModelBehavior`, and the
model is free to call the tool again.

**That default has a cost.** The tool keeps executing on later calls, so any
side effect it has repeats after its budget is spent. Two things bound it: the
model taking the failed result's "do not retry" at its word, and pydantic-ai's
default request limit, which ends a run that keeps calling. Set run-level `UsageLimits`
when a tool's side effects matter.

To have a spent budget end the run, name the exception in `reraise`:

```python
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from pydantic_ai.exceptions import UnexpectedModelBehavior
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

AgentConfig(
    model=...,
    tool_failure=ToolFailureConfig(
        reraise=(UnexpectedModelBehavior, DjangoPermissionDenied, DRFPermissionDenied),
    ),
)
```

Two caveats:

- **`reraise` replaces the default set, it does not extend it.** The default is
  `django.core.exceptions.PermissionDenied` and, when DRF is installed,
  `rest_framework.exceptions.PermissionDenied`
  ([a denial is not a tool failure](#a-denial-is-not-a-tool-failure)). A tuple
  that leaves them out converts denials again, so re-list both.
- **It ends the run for every `UnexpectedModelBehavior` a tool raises**, not
  only for its own spent budget: a tool that lets a sub-agent's exhausted
  budget propagate ends the run too. The two cannot be told apart, because the
  exception's `__cause__` shows `ModelRetry` in both.

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
  too, such as pydantic-ai-harness's guardrail and tool-call judge, so the
  record carries the arguments after their rewrites and a call they veto never
  reaches audit's `before_tool_execute`.
- **The failure policy is outermost**, and `build_agent` places it first, which
  keeps it outside other outermost capabilities. pydantic-ai runs error hooks
  innermost first, so the policy's runs last.
- **The guard is orthogonal.** It touches only `prepare_tools`, before any call
  is made.

Composing these by hand rather than through `build_agent`, keep the same
places: `AuditCapability` after any other innermost capability, and
`ToolFailurePolicy` before any other outermost one.

### A capability that sorts after audit

A capability that sorts after audit **runs between audit and the tool**. Three
compositions put one there, whatever `build_agent` did:

- an **innermost capability passed to a single run**, as in
  `agent.run(..., capabilities=[...])`, which pydantic-ai sorts after the
  agent's own capabilities;
- an innermost capability **composed by hand after audit**;
- a capability whose **own ordering places it inside audit**, wherever it is
  composed, `AgentConfig.capabilities` included, as
  `CapabilityOrdering(position="innermost", wrapped_by=[AuditCapability])`
  does.

**The rule: what such a capability does can reach the record, on every
pydantic-ai release and through any of its hooks, and the record is then not
the tool's own.** Which of its hooks run between audit and the tool, and when,
depends on the release. The examples below were measured with real agent
runs; they are examples, not a list of the only ways.

- **From pydantic-ai 2.54, through its `before_tool_execute`,
  `on_tool_execute_error` and `after_tool_execute`.** A record misses its
  argument rewrite; records a `ModelRetry` its `before_tool_execute` raises as
  a failure, though the tool never ran; records its recovery as a success,
  measuring the recovered value, and an exception it raises in place of the
  tool's; records a `ModelRetry` its `after_tool_execute` raises as a failure,
  though the tool succeeded; measures its result rewrite; and times its hooks
  with the tool.
- **From 2.54, through its `wrap_tool_execute`, when the tool raised
  `ModelRetry` or `ToolFailed`.** pydantic-ai routes both past every error and
  result hook, audit's included, so the record is whatever this wrapper hands
  back. A record carries its rewrite of the failure's message, as
  pydantic-ai-harness's `result_guard` does; records its recovery as a
  success; and records an exception it raises in their place. If it runs the
  tool again, the record describes the later run alone, and the run that
  failed has none; if a capability ahead of audit refuses that rerun, the
  record pairs the first run's arguments with the refusal, timed across both.
- **From 2.54, a rerun by its `wrap_tool_execute` after a failure audit's
  error hook saw, or after a success.** Audit's own hook settled the first
  run, so the tool gets one record, of that run. Reruns it makes
  concurrently, as through `asyncio.gather`, share one record, which can pair
  one run's arguments with another's result.
- **Before 2.54, through its `wrap_tool_execute`.** A record misses an argument
  rewrite there; records a recovery as a success, an exception of its own in
  place of the tool's, and a `ModelRetry` raised before the tool runs as a
  failure; measures a result rewrite; carries a rewrite of a failed tool's
  message; and times the wrapper with the tool. If it runs the tool again,
  the one record holds the last run's outcome, with the arguments audit passed
  on and the time of every run.

A veto it raises is still not recorded on any release, because a
`SkipToolExecution` is a call that did not execute.

**Everything composed through `AgentConfig.capabilities` is unaffected,
unless its own ordering places it inside audit.** `build_agent` appends audit
after all of it, so audit sorts last among the innermost capabilities there,
and the record is the tool's own on every release. pydantic-ai-harness's tool
guardrail and tool-call judge are innermost: in `config.capabilities` they sort
before audit, and passed to a single run they sort after it. There, from 2.54,
a guardrail's `retry` verdict is recorded as a failure, a `retry` from its
`result_guard` turns the tool's success into a failure, and its
`result_guard`'s `replace` is what the record measures. Its `result_guard`
also screens the message of a tool that raised `ModelRetry` or `ToolFailed`,
from its wrapper, so on every release a `replace` there is the message the
record names.

### Why audit needs all four hooks

pydantic-ai 2.54 moved every capability's `before_tool_execute`,
`on_tool_execute_error` and `after_tool_execute` inside the
`wrap_tool_execute` chain; earlier releases ran them around it. A wrapper that
recorded only what it saw would therefore describe a different thing on each
side of 2.54. From 2.54 it would record a failure another capability recovers
from as a success, a vetoed call as a failure, the arguments before a rewrite
and the result after one, and other capabilities' hooks in the duration.

For everything that sorts ahead of it, audit records the same thing on both,
because it observes the tool from all four hooks. Its `before_tool_execute` captures the arguments and the start, its
`on_tool_execute_error` the tool's exception, and its `after_tool_execute` the
tool's result. Its wrapper is the only one that writes the record, once per
execution, as it exits, preferring what the hooks captured over what it saw
itself. Before 2.54 the innermost wrapper encloses the tool alone, so what it
sees is already the tool's outcome; from 2.54 it encloses every hook, and the
captures are what keep the record the tool's. Each call's captures belong to
that call, so parallel calls in one run and concurrent runs of one agent never
read each other's. Reruns that a capability sorted after audit makes
concurrently, inside one call, are one call to audit, and share its captures.

Full signatures in the [policy reference](reference/policy.md).
