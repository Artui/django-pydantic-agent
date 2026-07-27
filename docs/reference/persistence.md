# Persistence

Store contracts, their value types, and the shipped implementations. See
[Storage](../storage.md) for owner scoping, thread keying, and the reference
models.

## Conversations

### `ConversationStore`

::: django_pydantic_agent.ConversationStore

### `Conversation`

::: django_pydantic_agent.Conversation

### `ConversationMeta`

::: django_pydantic_agent.ConversationMeta

### `NullConversationStore`

::: django_pydantic_agent.NullConversationStore

### `DjangoSessionConversationStore`

::: django_pydantic_agent.DjangoSessionConversationStore

### `ModelConversationStore`

::: django_pydantic_agent.ModelConversationStore

### `ScopedConversationStore`

::: django_pydantic_agent.ScopedConversationStore

## Attachments

### `AttachmentStore`

::: django_pydantic_agent.AttachmentStore

### `AttachmentRef`

::: django_pydantic_agent.AttachmentRef

### `OpenedAttachment`

::: django_pydantic_agent.OpenedAttachment

### `NullAttachmentStore`

::: django_pydantic_agent.NullAttachmentStore

### `ModelAttachmentStore`

::: django_pydantic_agent.ModelAttachmentStore

## Errors

### `AnonymousOperationError`

::: django_pydantic_agent.AnonymousOperationError

## Reference implementations

The opt-in `django_pydantic_agent.contrib.store` app. Add it to
`INSTALLED_APPS` and run `migrate`; the base package ships no model, so projects
that don't opt in get no migration.

::: django_pydantic_agent.contrib.store.default_conversation_store.DefaultConversationStore

::: django_pydantic_agent.contrib.store.default_attachment_store.DefaultAttachmentStore

::: django_pydantic_agent.contrib.store.default_step_store.DefaultStepStore
