"""Settings-agnostic Django agent-host substrate for Pydantic-AI."""

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.build_tool_catalog import build_tool_catalog
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.agent.types.agent_factory_fn import AgentFactoryFn
from django_pydantic_agent.agent.types.attachment_inline_config import AttachmentInlineConfig
from django_pydantic_agent.constants import (
    DESTRUCTIVE_METADATA_KEY,
    X_CATEGORY_KEY,
    X_CONFIRM_KEY,
    X_DESTRUCTIVE_KEY,
    X_SUMMARY_KEY,
    ToolCategory,
)
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_pydantic_agent.persistence.memory_namespace import memory_namespace
from django_pydantic_agent.persistence.model_attachment_store import ModelAttachmentStore
from django_pydantic_agent.persistence.model_conversation_store import ModelConversationStore
from django_pydantic_agent.persistence.null_attachment_store import NullAttachmentStore
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.persistence.scoped_conversation_store import ScopedConversationStore
from django_pydantic_agent.persistence.types.attachment_ref import AttachmentRef
from django_pydantic_agent.persistence.types.attachment_store import AttachmentStore
from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import ConversationMeta
from django_pydantic_agent.persistence.types.conversation_store import ConversationStore
from django_pydantic_agent.persistence.types.opened_attachment import OpenedAttachment
from django_pydantic_agent.policy.audit.audit_capability import AuditCapability
from django_pydantic_agent.policy.audit.logging_audit_logger import LoggingAuditLogger
from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger
from django_pydantic_agent.policy.failure.tool_failure_policy import ToolFailurePolicy
from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.guard.tool_guard import ToolGuard
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_pydantic_agent.registry.build_input_schema import build_input_schema
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from django_pydantic_agent.registry.types.tool_binding import ToolBinding
from django_pydantic_agent.registry.types.tool_spec import ToolSpec
from django_pydantic_agent.version import __version__

__all__ = [
    "DESTRUCTIVE_METADATA_KEY",
    "AgentConfig",
    "AgentDeps",
    "AgentFactoryFn",
    "AnonymousOperationError",
    "AttachmentInlineConfig",
    "AttachmentRef",
    "AttachmentStore",
    "AuditCapability",
    "AuditEvent",
    "AuditLogger",
    "Conversation",
    "ConversationMeta",
    "ConversationStore",
    "DjangoSessionConversationStore",
    "LoggingAuditLogger",
    "ModelAttachmentStore",
    "ModelConversationStore",
    "NullAttachmentStore",
    "NullAuditLogger",
    "NullConversationStore",
    "OpenedAttachment",
    "ScopedConversationStore",
    "ToolBinding",
    "ToolCategory",
    "ToolFailureConfig",
    "ToolFailurePolicy",
    "ToolGuard",
    "ToolGuardConfig",
    "ToolRegistry",
    "ToolSpec",
    "X_CATEGORY_KEY",
    "X_CONFIRM_KEY",
    "X_DESTRUCTIVE_KEY",
    "X_SUMMARY_KEY",
    "__version__",
    "build_agent",
    "build_input_schema",
    "build_tool_catalog",
    "memory_namespace",
    "tool",
]
