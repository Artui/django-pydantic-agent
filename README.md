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

Optional extras: `[drf-mcp]` (bridge a `drf-mcp-server` registry as a toolset),
`[spec-tools]` (drf-services specs as agent tools), `[harness]`
(`pydantic-ai-harness` capabilities), and the provider extras `[anthropic]` /
`[openai]` / `[google]`.

Docs: <https://artui.github.io/django-pydantic-agent/>
