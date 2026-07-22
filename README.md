# django-pydantic-agent

[![PyPI](https://img.shields.io/pypi/v/django-pydantic-agent.svg)](https://pypi.org/project/django-pydantic-agent/)
[![Python](https://img.shields.io/pypi/pyversions/django-pydantic-agent.svg)](https://pypi.org/project/django-pydantic-agent/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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

Optional extras: `[drf-mcp]` (bridge a `drf-mcp-server` registry as a toolset),
`[spec-tools]` (drf-services specs as agent tools), `[harness]`
(`pydantic-ai-harness` capabilities), and the provider extras `[anthropic]` /
`[openai]` / `[google]`.

Docs: <https://artui.github.io/django-pydantic-agent/>
