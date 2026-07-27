# Registry

Server-side tools and their derived schemas. See [Tools](../tools.md) for the
narrative.

## `ToolRegistry`

::: django_pydantic_agent.ToolRegistry

## `tool`

::: django_pydantic_agent.tool

## `ToolSpec`

::: django_pydantic_agent.ToolSpec

## `ToolBinding`

::: django_pydantic_agent.ToolBinding

## `build_input_schema`

::: django_pydantic_agent.build_input_schema

## `ToolCategory`

::: django_pydantic_agent.ToolCategory

## Schema and metadata keys

The `x-*` keys are JSON-Schema extensions that reach the **client** on a tool's
schema. `DESTRUCTIVE_METADATA_KEY` is different: it rides pydantic-ai's tool
metadata and is read **server-side** by the [tool guard](policy.md), which needs
destructiveness for tools whose flag doesn't come from the `@tool` registry.

::: django_pydantic_agent.constants
    options:
      members:
        - X_DESTRUCTIVE_KEY
        - X_CATEGORY_KEY
        - X_CONFIRM_KEY
        - X_SUMMARY_KEY
        - DESTRUCTIVE_METADATA_KEY
