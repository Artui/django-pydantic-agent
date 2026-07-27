# Integrations

Optional bridges exposing an existing DRF surface as agent tools. Both are
lazily imported and live under `django_pydantic_agent.integrations` rather than
being re-exported at the package root, so the base install pulls neither
dependency. See [Integrations](../integrations.md) for which to choose.

## Spec tools (`[spec-tools]`)

### `build_spec_capability`

::: django_pydantic_agent.integrations.build_spec_capability.build_spec_capability

### `resolve_spec_mapping`

::: django_pydantic_agent.integrations.resolve_spec_mapping.resolve_spec_mapping

### `SpecSource`

::: django_pydantic_agent.integrations.types.spec_source.SpecSource

## MCP tools (`[drf-mcp]`)

### `DRFMCPToolset`

::: django_pydantic_agent.integrations.drf_mcp.DRFMCPToolset
