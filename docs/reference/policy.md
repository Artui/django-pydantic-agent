# Policy

Audit, the destructive-tool gate and the tool-failure policy all ride the
Pydantic-AI capability seam. The first two are off by default; the third is on.
See [Policy](../policy.md) for the narrative.

## Audit

### `AuditLogger`

::: django_pydantic_agent.AuditLogger

### `AuditEvent`

::: django_pydantic_agent.AuditEvent

### `AuditCapability`

::: django_pydantic_agent.AuditCapability

### `NullAuditLogger`

::: django_pydantic_agent.NullAuditLogger

### `LoggingAuditLogger`

::: django_pydantic_agent.LoggingAuditLogger

## Tool guard

### `ToolGuardConfig`

::: django_pydantic_agent.ToolGuardConfig

### `ToolGuard`

::: django_pydantic_agent.ToolGuard

## Tool failure

### `ToolFailureConfig`

::: django_pydantic_agent.ToolFailureConfig

### `ToolFailurePolicy`

::: django_pydantic_agent.ToolFailurePolicy
