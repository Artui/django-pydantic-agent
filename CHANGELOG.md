# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **`[harness]` now requires `pydantic-ai-harness>=0.12,<0.13`** (was `>=0.7,<0.8`).
  The ceiling had gone five minors stale, which on a 0.x library is where breakage
  accumulates — and it did. It also gated
  [`pydantic_ai_harness.skills`](https://github.com/pydantic/pydantic-ai-harness/pull/396),
  which does not exist below 0.11 and is the prerequisite for adopting agent skills.
- **⚠ `DefaultStepStore.latest_snapshot()` gained an `include_interrupted`
  keyword**, matching the harness's `StepStore` protocol. Harness's own
  `continue_run()` passes it, so before this the resume path raised
  `TypeError: latest_snapshot() got an unexpected keyword argument`. **Any custom
  `StepStore` implementation must add the same parameter.**

### Added

- **⚠ New migration `0002_snapshot_state` — run `migrate` when upgrading.**
  `StoredSnapshot` gains a `state` column mirroring the harness's `SnapshotState`
  (`complete` / `interrupted`, defaulting to `complete`, so existing rows keep
  today's behaviour).

  A `complete` snapshot sits at a boundary where every tool call has a matching
  return, so resuming from it is always safe. An `interrupted` one is a rescue
  point captured mid-tool-cycle — pending calls may be re-executed or closed out
  with synthesized returns — so `latest_snapshot()` now **skips interrupted rows
  unless asked for them**. The state has to be *stored* rather than inferred:
  by the time a resume is attempted, the run that produced the row is gone.

  When several snapshots exist, the state filter applies **after** ordering, so
  the newest `complete` row wins even when newer `interrupted` rows sit above it
  — the same walk-back-from-newest the harness reference stores do.

### Changed

- **Raise the drf-chain ceilings: `[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.18`
  (was `>=0.15,<0.16`) and `[spec-tools]` → `djangorestframework-pydantic-ai>=0.9,<0.10`
  (was `>=0.8,<0.9`).** The MCP ceiling had gone stale a wave earlier — drf-mcp
  0.16.0 (MCP Apps) was already excluded — so two upstream releases were
  unreachable from here rather than one. **No adaptation was needed**, which the
  three relevant upstream changes explain:
  - **MCP Apps (drf-mcp 0.16.0)** adds `ui://` resources and `_meta.ui` links on
    tool definitions. The bridge reads `name` / `description` / `inputSchema` /
    `outputSchema` / `annotations` off `tools/list` and ignores `_meta`, so the
    addition is inert here. The resource-encoding fix in the same release (non-JSON
    resource bodies no longer come back as quoted JSON string literals) touches
    the resource surface, which this bridge does not use — it calls tools only.
  - **The shared `UrlKwarg` / `QueryParam` (drf-mcp 0.17.0, PAI 0.9.0)** are
    re-exported from `djangorestframework-services` rather than defined locally,
    behind permanently preserved import paths. Neither is imported here. PAI's
    switch from `ValueError` to `ImproperlyConfigured` for a bad channel
    declaration is likewise unreachable: `SpecCapability` is constructed with a
    spec mapping and no channel registrations.
  - **`InputRequired` enforcement (drf-services 0.28)** makes a missing
    marked-required input raise `ServiceValidationError` at dispatch. Over the
    MCP bridge that already arrives as an `isError` result with
    `type == "validation_error"`, which `call_tool` maps to `ModelRetry` — so a
    spec adopting the marker gets a model-correctable failure through this path
    with no change here.

## [0.3.0] — 2026-07-27

### Added

- **`AgentDeps` — typed, per-run dependencies threaded through the agent.**
  `build_agent` now returns `Agent[AgentDeps, Any]` (`deps_type=AgentDeps`), so
  a transport hands each run an `AgentDeps(user=request.user)` and every tool,
  toolset and capability reads request-scoped values off `RunContext.deps` —
  pydantic-ai's own seam for exactly this.
  - **The acting user binds natively.** `djangorestframework-pydantic-ai`'s
    `SpecToolset` already defaults to reading `ctx.deps.user`; until now this
    package overrode that with a closure over the request, so the upstream
    default could never fire.
  - **It unblocks reusing a built agent.** A capability that closes over a
    request can only serve that request, forcing a per-request rebuild —
    schemas and all. Request-independent collaborators are the precondition for
    building once and binding the user per run.
  - **AG-UI shared state now has somewhere to land.** `AgentDeps` satisfies
    pydantic-ai's `StateHandler` protocol, so a run's `RunAgentInput.state` is
    validated into `deps.state` rather than dropped with a `UserWarning`. Seed
    `state` with a Pydantic model instance to get it validated against that
    model. **Inbound only** — nothing emits `STATE_SNAPSHOT` / `STATE_DELTA`
    back yet; a tool must return those as `ToolReturn` metadata.
  - `AgentDeps` is the one record here that is **not frozen**: the UI adapter
    assigns `deps.state = …` directly (it does *not* use `dataclasses.replace`,
    despite what the protocol's own comment suggests). Deps are per-run and
    never shared, so the mutability is contained.

### Changed

- **`AgentFactoryFn` now returns `Agent[AgentDeps, Any]`.** A project using the
  `agent_factory=` escape hatch must build its agent with
  `deps_type=AgentDeps`; one that omits it produces an agent whose tools see no
  acting user. Type checkers flag it, which is how this surfaced.
- **`build_spec_capability(specs, request, …)` → `build_spec_capability(specs, …)`
  — the `request` argument is gone.** It existed solely to supply
  `get_user=lambda _ctx: request.user`; with typed deps, `SpecToolset`'s own
  default reads `ctx.deps.user` and the override is unnecessary. **Breaking for
  direct callers** (pre-1.0): drop the positional `request`, and make sure runs
  are given `deps=AgentDeps(user=…)`, or spec tools will act as `None`.

## [0.2.0] — 2026-07-27

### Added

- **Spec composition accepts a spec registry, not just a mapping.**
  `build_spec_capability(specs, request, …)` now takes either a
  `name -> spec` mapping or a **spec registry** — drf-services 0.27's
  `SpecRegistry`, the one declaration site for a project exposing the same specs
  over more than one transport. New `resolve_spec_mapping()` helper and a
  `SpecSource` Protocol.
  - **Matched structurally, not imported.** `SpecSource` declares only
    `specs() -> dict`, so this substrate still names no drf-services type and
    still depends on `pydantic-ai-slim` alone — drf-services arrives only with
    the optional `[spec-tools]` extra. Naming `SpecRegistry` in a signature
    would either force the dependency on every install (including projects whose
    tools are plain `@tool` functions) or bury the type behind a lazy import
    where it cannot appear in a signature at all. drf-services duck-types
    `SelectorSpec.filter_set` for the same reason.
  - **`resolve_spec_mapping()` is public on purpose.** A transport needs the
    same normalisation *before* the builder runs: iterating a registry yields
    `RegisteredSpec` records, not names, so a transport reserving tool names by
    iterating the raw argument would fill its collision-detection set with
    dataclasses and silently stop detecting duplicates between the `@tool`
    registry, the drf-mcp bridge and the spec tools.

### Changed

- **`[spec-tools]` now requires `djangorestframework-pydantic-ai>=0.8,<0.9`**
  (was `>=0.5,<0.6`) — registry support landed in PAI 0.8.
- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.15,<0.16`**
  (was `>=0.12,<0.13`). This is **not** optional housekeeping: drf-mcp 0.12 caps
  drf-services at `<0.26` while PAI 0.8 requires `>=0.27`, so moving only
  `[spec-tools]` makes the two extras **mutually uninstallable**. They have to
  advance together.

### Documentation

- **A real documentation site.** The package shipped in 0.1.0 with 36 exported
  symbols, a one-paragraph `docs/index.md` and a nav containing only "Home" — no
  reference at all. It now has five narrative pages (Concepts, Tools, Storage,
  Policy, Integrations) and a five-page autodoc reference covering the whole
  public surface, plus a README that says what the package does rather than only
  what it is. Highlights the things the source knows but nothing surfaced:
  capabilities compose order-independently; `category` is advisory while
  `destructive` drives the gate; the `x-*` schema keys reach the *client* while
  `DESTRUCTIVE_METADATA_KEY` is read *server-side*; stores key on
  `(owner_id, thread_id)`, so two transports sharing one store share a user's
  thread list unless wrapped in `ScopedConversationStore`.
- **Corrected a claim that this package declares a `StepStore` protocol.** It
  does not — `DefaultStepStore` structurally satisfies **`pydantic-ai-harness`'s**
  protocol, which is upstream's. The repo conventions asserted otherwise in two
  places.

### Fixed

- **The spec-conventions test asserted behaviour that changed in PAI 0.6.0.**
  `SpecCapability.get_instructions()` has returned `None` since the conventions
  moved onto `SpecToolset.get_instructions()` (so they reach the model whether a
  toolset is attached directly or wrapped, and are collected exactly once). The
  stale `[spec-tools]` ceiling pinned this package below 0.6, so the assertion
  kept passing against an API two minors old; raising the pin surfaced it. The
  test now reads the instructions off the toolset, and
  `build_spec_capability`'s module docstring no longer claims the capability
  emits them.

## [0.1.0] — 2026-07-23

### Added

- **Initial extraction** of the settings-agnostic agent-host substrate from
  `django-ag-ui`: `AgentConfig` + `build_agent`, the `ToolRegistry` / `@tool`
  registry and typed schema derivation, toolset & capability composition
  (including the optional `[drf-mcp]` bridge and `[spec-tools]` capability), the
  `AuditLogger` protocol and audit capability, the `ToolGuard` policy, the
  `get_user` / authorization helpers, the `ConversationStore` / `AttachmentStore`
  / `StepStore` contracts, and the reference `contrib.store` models and stores.
- **The core reads no Django settings.** Anything that previously resolved from
  `DJANGO_AG_UI` is now an explicit argument — notably the model stores take
  `allow_anonymous: bool = False` rather than consulting a settings key.
- **`Conversation.messages` is transport-owned.** The core persists and returns
  JSON-serialisable message records verbatim and never interprets them, so it
  carries no dependency on any wire format; the calling transport validates its
  own shape (and its message ids survive a round trip untouched).

[Unreleased]: https://github.com/Artui/django-pydantic-agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/django-pydantic-agent/releases/tag/v0.1.0
