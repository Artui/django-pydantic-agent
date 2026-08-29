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

## Memory

### `memory_namespace`

::: django_pydantic_agent.memory_namespace

### `memory_namespace_for_user`

::: django_pydantic_agent.memory_namespace_for_user

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

::: django_pydantic_agent.contrib.store.default_memory_store.DefaultMemoryStore

### Attachment lifecycle

Contrib-level, not part of either protocol. See
[Storage](../storage.md#attachment-lifecycle) for the semantics and the
management commands.

::: django_pydantic_agent.contrib.store.reconcile_conversation_attachments.reconcile_conversation_attachments

::: django_pydantic_agent.contrib.store.strip_inline_binary_parts.strip_inline_binary_parts

::: django_pydantic_agent.contrib.store.types.attachment_deletion.AttachmentDeletion
