# Moving to django-spec-agents

`django-pydantic-agent` 0.28.3 is its final release, and it is code-identical to
0.28.2. Development continues as
[`django-spec-agents`](https://pypi.org/project/django-spec-agents/), whose
import package is `django_spec_agents` and whose documentation is at
<https://artui.github.io/django-spec-agents/>. It merges this package with
`djangorestframework-pydantic-ai`, and this package's layout carries over under
the new root, so `django_pydantic_agent.contrib.store` is
`django_spec_agents.contrib.store` and so on.

One module did not carry over. `django_pydantic_agent.constants` is gone: the
enums and constants live in one module each under `django_spec_agents.types`, and
the names `ToolCategory`, `X_DESTRUCTIVE_KEY`, `X_CATEGORY_KEY`, `X_CONFIRM_KEY`,
`X_SUMMARY_KEY` and `DESTRUCTIVE_METADATA_KEY` keep their spelling and are
imported from the `django_spec_agents` root. `ATTACHMENT_STORAGE_ALIAS` is not a
root export; import it from `django_spec_agents.types.attachment_storage_alias`.

## Moving an installed project

1. Run `migrate` on this release, `django-pydantic-agent` 0.28.3, first. The new
   store's migration renames this package's store tables in place and needs all
   of this package's own store migrations applied before it runs.
2. Replace the dependency with `django-spec-agents` and change every
   `django_pydantic_agent` import to `django_spec_agents`, importing what was in
   `django_pydantic_agent.constants` as described above.
3. Change the `INSTALLED_APPS` entry `"django_pydantic_agent.contrib.store"` to
   `"django_spec_agents.contrib.store"`.
4. Run `migrate` once. The new app's label is `django_spec_agents_store`, and its
   single migration renames the store's tables in place.

### Your own migrations

A project app whose models or migrations name the old store label
`django_pydantic_agent_store` stops loading once that app leaves
`INSTALLED_APPS`: Django raises `NodeNotFoundError` for a migration dependency
such as `("django_pydantic_agent_store", "0005_storedmemory_storedmemoryoperation")`,
and the system check rejects a foreign key such as
`"django_pydantic_agent_store.StoredConversation"`. Edit them to the new label:

- change the foreign key target in the model and in the migration to
  `django_spec_agents_store.<Model>`;
- change the migration dependency to `("django_spec_agents_store", "0001_initial")`,
  the new app's only migration.

Then the first `migrate` needs one extra step. Your migration is recorded as
applied and now depends on `django_spec_agents_store.0001_initial`, which is not
applied yet, and Django refuses that history
(`InconsistentMigrationHistory`). Run `migrate` once with the apps that hold
such migrations left out of `INSTALLED_APPS`, so the store's tables are renamed
first, and then put them back: the rows your foreign keys point at are kept. A
foreign key from a project model to a store model, migrated on 0.28.2 and then
moved this way, kept its row and left `makemigrations --check` clean.

## Extras

`anthropic`, `openai`, `google`, `drf-mcp` and `harness` keep their names in
`django-spec-agents`. The `[spec-tools]` extra, which pulled
`djangorestframework-pydantic-ai`, becomes `django-spec-agents[specs]`.

## Names that keep their spelling

These are stored or configured outside Python, so settings, sessions, stored
files and `LOGGING` keep working. All but the management commands keep the
`django_pydantic_agent` spelling:

- the attachment storage alias `django_pydantic_agent_attachments`;
- the session key `django_pydantic_agent_conversations`;
- the attachment upload prefix `django_pydantic_agent/attachments/`;
- the `django_pydantic_agent.audit` and `django_pydantic_agent.failure` loggers;
- the `agent_store_*` management commands, which keep their names.

## Names that change

- `DESTRUCTIVE_METADATA_KEY` is `"django_spec_agents.destructive"`.
- The attachments toolset id is `django-spec-agents-attachments`.
- The audit `ContextVar` is named `django_spec_agents.audit.execution`.
- Model-build and failure-policy messages start `django-spec-agents: `.
- A registry tool registered with `@tool(registry, name="...")`, or a `ToolSpec`
  registered directly, is offered under its registered name. This package offers
  the function's own name, so a `require_approval`, an `exempt`, an approval
  prompt, an audit query or a query on the step store's `tool_name` that named
  such a tool by its function name uses the registered name after moving.
- A `description=` override reaches the model. This package drops it.

## A known defect in this final release

In this package, a destructive tool whose registered name differs from its
function's name, through `@tool(name=)` or a `ToolSpec` registered directly, is
offered to the model under the function's name, so the tool guard does not ask
before it runs. The registry hands the agent the bare functions, which is also
why a `description=` override is dropped. There is no patch here: it is fixed
only in `django-spec-agents`, where such a tool is offered under its registered
name.

If you stay on 0.28.x for now, put the function's name in `require_approval`:
that gates the tool, and the registered name does nothing there. Replace it with
the registered name when you move, because after moving an entry left on the
function name no longer matches.
