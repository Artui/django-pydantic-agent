# django-pydantic-agent

The settings-agnostic agent-host substrate shared by Django's Pydantic-AI
transports.

It takes an `AgentConfig` in and returns a built `pydantic_ai.Agent` — with
composed toolsets, audit and user resolution — and reads **no** Django settings
of its own. Each transport ([`django-ag-ui`](https://github.com/Artui/django-ag-ui)
for AG-UI over SSE, `django-a2a` for agent-to-agent) owns its settings namespace,
builds an `AgentConfig`, and hands it down.

```bash
pip install django-pydantic-agent
```

## Is this the package you want?

Often not directly — **most projects install a transport** and get this as a
dependency. Reach for it on its own when you are:

- **building a transport** — a new wire protocol over the same agent host;
- **sharing agent construction** between transports, so one project's AG-UI
  endpoint and its A2A endpoint expose the same tools, audit trail and policy;
- **embedding an agent** with no HTTP surface at all — a management command, a
  background task — where you want the registry, audit and guard machinery
  without a view.

If you want an agent in a browser, start with
[`django-ag-ui`](https://github.com/Artui/django-ag-ui).

## What it provides

- **[Agent construction](concepts.md)** — `AgentConfig` → `build_agent`, model
  and provider resolution, and an escape hatch to replace construction entirely.
- **[A tool registry](tools.md)** — `@tool` with `destructive` / `category` /
  `confirm` / `summary` metadata, and JSON Schema derived from the signature.
- **[Storage contracts](storage.md)** — `ConversationStore` / `AttachmentStore`
  protocols, reference implementations, and Django models in `contrib.store`.
- **[Policy](policy.md)** — an audit trail over every tool call, and a
  server-side approval gate for destructive tools.
- **[Integrations](integrations.md)** — optional bridges exposing
  `djangorestframework-services` specs or a `drf-mcp-server` registry as agent
  tools.

## The rule that shapes everything

**The core reads no Django settings.** There is no `DJANGO_*` lookup anywhere in
the package. Its public input is an `AgentConfig` dataclass; each transport owns
its own settings namespace, resolves its config, and hands the record down.

That is what keeps transports independent — `django-ag-ui` reads `DJANGO_AG_UI`,
`django-a2a` reads its own, and neither fights the other for keys. A settings
read added here would turn every consumer's upgrade into a key migration. It is
also why collaborators are always passed as live objects rather than dotted
paths: there is no `import_string` in this package.

## Where next

- **[Concepts](concepts.md)** — how a config becomes an agent, and what belongs
  here versus in a transport.
- **[Tools](tools.md)** — registering tools and what the metadata does.
- **[Storage](storage.md)** — the store contracts and the reference models.
- **[Policy](policy.md)** — audit and the destructive-tool gate.
- **[Integrations](integrations.md)** — DRF specs and MCP tools as agent tools.
- **[Reference](reference/index.md)** — the full public API.
