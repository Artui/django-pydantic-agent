# Repo conventions for `django-pydantic-agent`

This file is the single source of truth for how to write code in this package.
Rules are non-negotiable unless flagged as a heuristic.

## What this package is

The **settings-agnostic agent-host substrate** shared by Django's Pydantic-AI
transports. `AgentConfig` in → a built `pydantic_ai.Agent` (composed toolsets,
audit, user resolution) out.

Provides:
- `AgentConfig` + the agent builder; model / model-settings / retries / provider
  resolution and the agent-factory escape hatch.
- A tool registry (`ToolRegistry`, `@tool`) with `destructive=` / `category=` /
  `confirm=` / `summary=` metadata and typed JSON-Schema derivation
  (`x-destructive` / `x-category` / `x-confirm` / `x-summary` — plain annotations
  any protocol can carry).
- Toolset / capability composition, including the optional `[drf-mcp]` bridge and
  `[spec-tools]` `SpecCapability`.
- The `AuditLogger` protocol (`Null` / `Logging`) and the `get_user(request)`
  user-resolution hook.
- Storage **contracts** — `ConversationStore` / `AttachmentStore` protocols —
  plus the reference `contrib.store` models and stores. (`DefaultStepStore`
  ships too, but its protocol is `pydantic-ai-harness`'s; this package does
  not declare a `StepStore` protocol of its own.)

### The one rule that defines this package

**The core reads no Django settings.** There is no `DJANGO_*` lookup anywhere in
`django_pydantic_agent`. Its public input is an `AgentConfig` dataclass; each
transport owns its own settings namespace, builds an `AgentConfig`, and hands it
down.

This is what keeps the transports independent: `django-ag-ui` keeps reading
`DJANGO_AG_UI`, `django-a2a` reads its own namespace, and neither fights the other
for keys. A settings read added here would turn every consumer's upgrade into a
key migration. **If you need a value, put it on `AgentConfig`.**

## What belongs here vs. in a transport

The litmus test: **if it maps agent output to a specific wire format, or serves a
specific frontend, it is a transport; everything upstream of "how do I speak to the
peer" is core.**

| Concern | Lands in |
| --- | --- |
| `AgentConfig`, model / settings / retries / provider resolution, agent-factory escape hatch | **core** |
| `ToolRegistry` + `@tool` + typed-schema derivation | **core** |
| External toolsets / capabilities; the `[drf-mcp]` bridge and `[spec-tools]` composition | **core** (optional extras) |
| `get_user(request)` + the `AuditLogger` protocol | **core** |
| `ConversationStore` / `AttachmentStore` protocols, the `read_attachment` tool, `contrib.store` models and stores (incl. `DefaultStepStore`, satisfying harness's protocol) | **core** (storage contracts are transport-neutral) |
| An HTTP view, an SSE/streaming encoder, a wire adapter, `.urls` | **a transport** |
| Browser-facing sub-views (thread drawer, attachments, transcription, skills palette) | **a transport** |

The stores are the non-obvious call: they are *contracts* (core), while the HTTP
*views* over them are per-transport — a thread drawer is a browser REST surface,
whereas an agent-to-agent peer models the same history as tasks/contexts entirely
differently. Reasoning is *produced* here (a model-settings thinking config) but
*mapped to wire events* by the transport.

## Commands

| Target | What it does |
| --- | --- |
| `make init` | `uv sync --all-groups` + install pre-commit hooks |
| `make test` | pytest with 100% line+branch coverage gate |
| `make lint` | `ruff check .` + `ty check django_pydantic_agent` |
| `make format` | `ruff format .` |
| `make docs-serve` | live-reload mkdocs at `localhost:8000` |
| `make docs-build` | `mkdocs build --strict` |
| `make release-bump VERSION=X.Y.Z` | rewrite `version.py` + promote `[Unreleased]` in CHANGELOG |
| `make release-publish` | end-to-end workstation release |

## Structural rules

1. **One exported class or function per file.** File name = `snake_case` of the symbol.
   `ToolRegistry` → `tool_registry.py`; `build_input_schema` → `build_input_schema.py`.
   **Exception:** `django_pydantic_agent/constants.py` is the package's single home for
   enums and constant-like module-level values, and is the only file allowed to export
   multiple symbols. Django `models.py` is the other necessary exception — Django
   requires models to live there.
2. **Private helpers used in only one file** stay there with a leading `_`.
3. **Non-exported helpers shared across files** go into a sibling `utils.py`. Classes are
   allowed in `utils.py` if they are internal infrastructure.
4. **Top-level imports only.** No function-local / lazy imports unless a circular import is
   genuine and documented inline at the import site, **or** the dependency is optional —
   those imports go inside the function body.
5. **Full type annotations on every function and method signature.** `Any` is allowed only at
   Django/Pydantic-AI boundaries where the type genuinely is `Any`.
6. **`__init__.py` is the only re-export point.** Each `__init__.py` lists the public surface
   in `__all__`. Internal modules import from leaf paths, never from the package's `__init__`.
7. **Always `from __future__ import annotations`** at the top of any file with type
   annotations. Python 3.10+, so no PEP 695 `type` statements.
8. **Absolute imports only.** Imports are ordered stdlib → third-party → first-party
   (`django_pydantic_agent`). Within each block, alphabetical.
9. **NEVER use relative imports.** `from . import x`, `from .foo import bar`, any dotted-
   relative form is forbidden everywhere in the package, including `__init__.py`.
10. **Types and functionality live in separate sub-packages.** When a directory contains both
    type declarations (dataclasses, Protocols, frozen wire-shape records) and functionality
    (callables, registries, dispatch helpers), the types move into a `types/` sibling.

## API style rules

11. **Always dataclasses over `dict[str, Any]` for structured data.** Every payload,
    configuration record, and tool spec field is a `@dataclass` with explicit field types.
    `dict[str, Any]` survives only at genuine serialisation boundaries.
12. **Tool callables are typed.** Every registered tool declares typed parameters and a typed
    return — no `**kwargs: Any` escape hatches. The registry derives JSON Schema from
    signatures; an untyped tool breaks the schema.
13. **Collaborators are constructor arguments, never dotted paths.** There is no
    `import_string` in this package. A caller that can hold a live object passes one.

## No module-level or class-level mutable state

State lives on instances. Module-level constants (lookup tables, regexes, dispatch tables)
are fine — module-level **mutable** state is not.

- No module-level mutable singletons (registries, caches, "warned-once" flags).
- No class-level mutable attributes declared on the class body. Initialise mutables in
  `__init__`.

## Tests

- `make test` runs pytest with `--cov=django_pydantic_agent --cov-fail-under=100`
  (line + branch). Restructure rather than reach for `# pragma: no cover`.
- Test layout mirrors the source tree under `tests/`.
  `django_pydantic_agent/foo/bar.py` → `tests/foo/test_bar.py`.
- `tests/conftest_settings.py` is the Django settings module pytest uses (set via
  `DJANGO_SETTINGS_MODULE` in `pyproject.toml`).
- Async tests: `async def test_...` with pytest-asyncio (`asyncio_mode = "auto"`).
- A store that writes through `sync_to_async` needs
  `@pytest.mark.django_db(transaction=True)` — its ORM calls run on a different
  connection than a transaction-wrapped test would roll back.

## Lint and types

- `make lint` runs `ruff check .` + `ty check django_pydantic_agent`. CI fails on either.
- `ruff format` is the source of truth for layout.
- Pre-commit runs `make lint-fix`, `make format`, `make type-check`. Commits must be clean
  before push — never `--no-verify`.
- `ty` is scoped to `django_pydantic_agent/` only (not tests).

## Compatibility floor

| Component | Floor | Tested |
| --- | --- | --- |
| Python | 3.10 | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2 LTS | 4.2, 5.0, 5.1, 5.2, 6.0 |
| Pydantic-AI | 2.0 (the capability seam is v2-only) | latest in matrix |

## Branching

When working on a new feature or version bump, **ALWAYS** switch to a new branch first
(`git checkout -b feat/...` or `release/vX.Y.Z`) and push to that branch. Never commit
feature work or version bumps directly to `main`, and never push to `main` from the local
checkout — `main` only advances via merged PRs.

## Releases

Merge-to-main triggered. `.github/workflows/release.yml` runs on tag push and publishes to
PyPI via OIDC trusted publishing, then deploys docs to `gh-pages`.

### Cutting a release

```bash
make release-bump VERSION=0.2.0
git diff
git commit -am "Release 0.2.0"
git push -u origin release/0.2.0
gh pr create
# Merge to main; release.yml fires on the merge commit.
```

The **first** release is the exception: the scaffold already sits at `0.1.0`, so write the
`## [0.1.0] — <date>` heading in the CHANGELOG by hand (there is no prior tag to bump from)
and let the tag push publish it.

### One-time setup (manual)

1. **PyPI Trusted Publisher** — `Artui/django-pydantic-agent`, workflow `release.yml`,
   environment `pypi`. For a project with no releases yet this is a *pending* publisher.
2. **GitHub Environment** — create `pypi` (no secrets; OIDC).
3. **GitHub Pages** — branch `gh-pages` (created on first release with docs).
