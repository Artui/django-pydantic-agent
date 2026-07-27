# Reference

Autodocumented from the package itself via `mkdocstrings` — every entry is
generated from the live source, nothing duplicated by hand.

- **[Agent](agent.md)** — `AgentConfig`, `build_agent`, `build_tool_catalog`,
  `AgentFactoryFn`.
- **[Registry](registry.md)** — `ToolRegistry`, `@tool`, `ToolSpec`,
  `ToolBinding`, `build_input_schema`, `ToolCategory` and the schema keys.
- **[Persistence](persistence.md)** — the store contracts, their value types,
  and the shipped implementations.
- **[Policy](policy.md)** — audit and the destructive-tool gate.
- **[Integrations](integrations.md)** — the `[spec-tools]` and `[drf-mcp]`
  bridges.

## Public surface

Everything below is importable from the top-level `django_pydantic_agent`
package. The two integration bridges are the exception: they live under
`django_pydantic_agent.integrations` and are imported lazily, so they are not
re-exported at the root.

```python
from django_pydantic_agent import (
    # agent
    AgentConfig,
    AgentFactoryFn,
    build_agent,
    build_tool_catalog,
    # registry
    ToolRegistry,
    ToolSpec,
    ToolBinding,
    tool,
    build_input_schema,
    ToolCategory,
    DESTRUCTIVE_METADATA_KEY,
    X_CATEGORY_KEY,
    X_CONFIRM_KEY,
    X_DESTRUCTIVE_KEY,
    X_SUMMARY_KEY,
    # persistence
    ConversationStore,
    Conversation,
    ConversationMeta,
    NullConversationStore,
    DjangoSessionConversationStore,
    ModelConversationStore,
    ScopedConversationStore,
    AttachmentStore,
    AttachmentRef,
    OpenedAttachment,
    NullAttachmentStore,
    ModelAttachmentStore,
    AnonymousOperationError,
    # policy
    AuditLogger,
    AuditEvent,
    AuditCapability,
    NullAuditLogger,
    LoggingAuditLogger,
    ToolGuard,
    ToolGuardConfig,
)
```
