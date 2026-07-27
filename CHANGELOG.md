# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Artui/django-pydantic-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/django-pydantic-agent/releases/tag/v0.1.0
