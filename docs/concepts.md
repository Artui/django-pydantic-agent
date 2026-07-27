# Concepts

## Config in, agent out

The whole package is one shape: a [`ToolRegistry`](tools.md) and an
`AgentConfig` go in, a `pydantic_ai.Agent` comes out.

```python
from django_pydantic_agent import AgentConfig, ToolRegistry, build_agent, tool

registry = ToolRegistry()


@tool(registry)
def list_orders(status: str) -> list[dict]:
    """List orders with the given status."""
    ...


agent = build_agent(
    registry,
    AgentConfig(
        model="anthropic:claude-sonnet-4-5",
        instructions="You help staff manage orders.",
    ),
)
```

`AgentConfig` is a frozen dataclass carrying the model, instructions,
`model_settings`, `retries`, extra `toolsets` and `capabilities`, an
`audit_logger`, and a `tool_guard` policy. Everything is optional except
`model`.

A transport resolves those values from *its* settings namespace and hands the
record down. This package never reads them itself.

## What gets composed into the agent

`build_agent` registers each registry tool as a plain Pydantic-AI tool, then
composes capabilities:

- **`AuditCapability`**, when `audit_logger` is set and isn't the null logger.
  It rides the `wrap_tool_execute` lifecycle hook, so it times and records
  **every** tool the agent runs — registry tools *and* composed toolsets alike,
  not just the ones registered here.
- **`ToolGuard`**, when `tool_guard` is set and `enabled`. It flips destructive
  tools to require approval.
- Anything in `config.capabilities`, verbatim.

Capabilities are composed **order-independently**. Each declares its position
via `get_ordering()` (audit is outermost; the guard is orthogonal), and
pydantic-ai's `CombinedCapability` topologically sorts them — so the list
`build_agent` assembles needn't be pre-ordered, and a transport appending its
own capability doesn't have to think about where.

The agent's `output_type` includes `DeferredToolRequests`, which turns on the
tool-approval interrupt loop for **server-side** tools. That is deliberate and
not merely a default: the AG-UI adapter only augments `output_type` when a run
carries *frontend* tools, so a run whose only gated tool is server-side would
otherwise never defer. Setting it here makes the approval path independent of
whether the client declared any tools of its own.

## Per-run dependencies

The agent is typed `Agent[AgentDeps, ...]`, so every run is given an
`AgentDeps` — pydantic-ai's own seam for request-scoped values. Tools, toolsets
and capabilities read them off `RunContext.deps`:

```python
deps = AgentDeps(user=request.user)
```

This is what a transport passes as `deps=` when it starts a run, and it is what
replaced closing over the request. The difference matters beyond tidiness:

- **The acting user binds natively.** `djangorestframework-pydantic-ai`'s
  `SpecToolset` already defaults to reading `ctx.deps.user`, so spec tools act
  as the right user with nothing passed at the call site.
- **The agent stops being request-shaped.** A capability that closes over a
  request can only serve that request, which forces a rebuild — schemas and all
  — per call. Request-independent collaborators are the precondition for
  reusing a built agent across runs.
- **AG-UI state has somewhere to land.** `AgentDeps` satisfies pydantic-ai's
  `StateHandler` protocol, so a run's `RunAgentInput.state` is validated into
  `deps.state` instead of being dropped with a warning.

`AgentDeps` is deliberately **not** frozen, unlike every other record here: the
UI adapter assigns `deps.state = ...` directly. Deps are per-run and never
shared, so the mutability is contained.

Projects needing more per-run context subclass it — `user` and `state` are the
two fields the framework itself reads.

!!! note "Inbound state only"

    Nothing emits `STATE_SNAPSHOT` / `STATE_DELTA` back to the client yet. A run
    *receives* client state; a tool returning those events as `ToolReturn`
    metadata is a separate piece of work.

## Model resolution

`AgentConfig.model` takes whatever Pydantic-AI takes — a `"provider:model"`
string or a `Model` instance.

`pydantic-ai-slim` ships no providers, so a provider string needs its library
installed. The extras exist for exactly that:

```bash
pip install django-pydantic-agent[anthropic]   # or [openai], [google]
```

## Replacing construction entirely

When `build_agent`'s composition isn't what a project wants — a custom output
type, an unusual toolset arrangement, bespoke instrumentation — a transport can
accept an `AgentFactoryFn` instead. It receives the tool registry and *that
transport's* resolved config object, and fully replaces `build_agent`.

The config argument is deliberately untyped: this substrate reads no settings
and owns no settings namespace, so the second argument is whatever configuration
record the calling transport passes down.

## Core versus transport

The litmus test: **if it maps agent output to a specific wire format, or serves a
specific frontend, it is a transport; everything upstream of "how do I speak to
the peer" is core.**

| Concern | Lands in |
| --- | --- |
| `AgentConfig`, model / settings / retries resolution, the factory escape hatch | **core** |
| `ToolRegistry` + `@tool` + typed schema derivation | **core** |
| External toolsets and capabilities; the `[drf-mcp]` and `[spec-tools]` bridges | **core** (optional extras) |
| The `AuditLogger` protocol and the tool guard | **core** |
| `ConversationStore` / `AttachmentStore` protocols, `contrib.store` models | **core** |
| An HTTP view, an SSE encoder, a wire adapter, `.urls` | **a transport** |
| Browser-facing sub-views (thread drawer, attachments, transcription) | **a transport** |

The stores are the non-obvious call: they are *contracts*, which are core, while
the HTTP *views* over them are per-transport. A thread drawer is a browser REST
surface; an agent-to-agent peer models the same history as tasks and contexts
entirely differently, so the view can't be shared even though the storage can.

Reasoning is the mirror image — it is *produced* here (a model-settings thinking
config) but *mapped to wire events* by the transport.
