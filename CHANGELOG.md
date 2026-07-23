# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Artui/django-pydantic-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Artui/django-pydantic-agent/releases/tag/v0.1.0
