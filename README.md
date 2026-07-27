# django-pydantic-agent

[![CI](https://github.com/Artui/django-pydantic-agent/workflows/tests/badge.svg)](https://github.com/Artui/django-pydantic-agent/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-pydantic-agent.svg)](https://pypi.org/project/django-pydantic-agent/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-pydantic-agent.svg)](https://pypi.org/project/django-pydantic-agent/)
[![Django versions](https://img.shields.io/pypi/djversions/django-pydantic-agent.svg)](https://pypi.org/project/django-pydantic-agent/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-pydantic-agent/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-pydantic-agent/gh-pages/coverage.json)](https://github.com/Artui/django-pydantic-agent/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-pydantic-agent.svg)](LICENSE)

The settings-agnostic agent-host substrate shared by Django's Pydantic-AI
transports.

It takes an `AgentConfig` in and returns a built `pydantic_ai.Agent` — with
composed toolsets, audit, and user resolution — and reads **no** Django settings
of its own. Each transport owns its settings namespace, builds an `AgentConfig`,
and hands it down:

- **[django-ag-ui](https://github.com/Artui/django-ag-ui)** — AG-UI over SSE for a
  browser client.
- **django-a2a** — agent-to-agent (planned).

```bash
pip install django-pydantic-agent
```

Most projects install a **transport** and get this as a dependency. Reach for it
directly when you are building a transport, sharing agent construction between
two of them, or embedding an agent with no HTTP surface at all.

```python
from django_pydantic_agent import AgentConfig, ToolRegistry, build_agent, tool

registry = ToolRegistry()


@tool(registry)
def list_orders(status: str) -> list[dict]:
    """List orders with the given status."""
    ...


agent = build_agent(registry, AgentConfig(model="anthropic:claude-sonnet-4-5"))
```

## What it provides

- **Agent construction** — `AgentConfig` → `build_agent`, model and provider
  resolution, and an escape hatch to replace construction entirely.
- **A tool registry** — `@tool` with `destructive` / `category` / `confirm` /
  `summary` metadata, and JSON Schema derived from the signature.
- **Storage contracts** — `ConversationStore` / `AttachmentStore` protocols,
  reference implementations, and Django models in `contrib.store`.
- **Policy** — an audit trail over every tool call, and a server-side approval
  gate for destructive tools. Both off by default.
- **Integrations** — optional bridges exposing `djangorestframework-services`
  specs or a `drf-mcp-server` registry as agent tools, in process.

Optional extras: `[drf-mcp]` (bridge a `drf-mcp-server` registry as a toolset),
`[spec-tools]` (drf-services specs as agent tools), `[harness]`
(`pydantic-ai-harness` capabilities), and the provider extras `[anthropic]` /
`[openai]` / `[google]`.

## Documentation

<https://artui.github.io/django-pydantic-agent/>

- [Concepts](https://artui.github.io/django-pydantic-agent/concepts/) — how a
  config becomes an agent, and what belongs here versus in a transport.
- [Tools](https://artui.github.io/django-pydantic-agent/tools/) — registering
  tools and what the metadata does.
- [Storage](https://artui.github.io/django-pydantic-agent/storage/) — the store
  contracts, owner scoping, and the reference models.
- [Policy](https://artui.github.io/django-pydantic-agent/policy/) — audit and the
  destructive-tool gate.
- [Integrations](https://artui.github.io/django-pydantic-agent/integrations/) —
  DRF specs and MCP tools as agent tools.
- [Reference](https://artui.github.io/django-pydantic-agent/reference/) — the
  full public API.
