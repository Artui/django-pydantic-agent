# Moving to django-spec-agents

`django-pydantic-agent` 0.28.3 is its final release, and it is code-identical to
0.28.2. Development continues as
[`django-spec-agents`](https://pypi.org/project/django-spec-agents/), whose
import package is `django_spec_agents` and whose documentation is at
<https://artui.github.io/django-spec-agents/>. It merges this package with
`djangorestframework-pydantic-ai`, and this package's layout carries over under
the new root, so `django_pydantic_agent.contrib.store` is
`django_spec_agents.contrib.store` and so on.

## Moving an installed project

1. Run `migrate` on this release, `django-pydantic-agent` 0.28.3, first. The new store's migration renames this package's store tables
   in place and needs all of this package's own store migrations applied before
   it runs.
2. Replace the dependency with `django-spec-agents` and change every
   `django_pydantic_agent` import to `django_spec_agents`.
3. Change the `INSTALLED_APPS` entry `"django_pydantic_agent.contrib.store"` to
   `"django_spec_agents.contrib.store"`.
4. Run `migrate` once. The new app's label is `django_spec_agents_store`, and its
   single migration renames the store's tables in place.

## Extras

`anthropic`, `openai`, `google`, `drf-mcp` and `harness` keep their names in
`django-spec-agents`. The `[spec-tools]` extra, which pulled
`djangorestframework-pydantic-ai`, becomes `django-spec-agents[specs]`.

## Names that keep their spelling

These are stored or configured outside Python, so they keep the
`django_pydantic_agent` spelling and your settings, sessions, stored files and
`LOGGING` keep working:

- the attachment storage alias `django_pydantic_agent_attachments`;
- the session key `django_pydantic_agent_conversations`;
- the attachment upload prefix `django_pydantic_agent/attachments/`;
- the `agent_store_*` management commands;
- the `django_pydantic_agent.audit` and `django_pydantic_agent.failure` loggers.

## Names that change

- `DESTRUCTIVE_METADATA_KEY` is `"django_spec_agents.destructive"`.
- The attachments toolset id is `django-spec-agents-attachments`.
- Model-build and failure-policy messages start `django-spec-agents: `.
- A registry tool registered with `@tool(registry, name="...")`, or a `ToolSpec`
  registered directly, is offered under its registered name. This package offers
  the function's own name, so a `require_approval`, an `exempt`, an approval
  prompt or an audit query that named such a tool by its function name uses the
  registered name after moving.

## A known defect in this final release

In this package, a destructive tool registered under a `name=` other than its
function's, or as a direct `ToolSpec`, is offered to the model under the
function's name, so the tool guard does not ask before it runs. There is no
patch here: it is fixed only in `django-spec-agents`, where such a tool is
offered under its registered name.
