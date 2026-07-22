# django-pydantic-agent

The settings-agnostic agent-host substrate shared by Django's Pydantic-AI
transports.

It takes an `AgentConfig` in and returns a built `pydantic_ai.Agent` — with
composed toolsets, audit and user resolution — and reads **no** Django settings
of its own. Each transport (`django-ag-ui` for AG-UI over SSE, `django-a2a` for
agent-to-agent) owns its settings namespace, builds an `AgentConfig`, and hands
it down.

```bash
pip install django-pydantic-agent
```
