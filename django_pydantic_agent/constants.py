from __future__ import annotations

from enum import Enum


class ToolCategory(str, Enum):
    """Coarse grouping for a tool, surfaced to the agent and the UI.

    Advisory metadata: it lets a frontend group tools, a system prompt reason
    about capability classes, and a project apply category-wide policy. It does
    **not** gate execution; that is the ``destructive`` flag's job.
    """

    SHELL = "shell"
    INTROSPECT = "introspect"
    NAV = "nav"
    UI_READ = "ui_read"
    UI_WRITE = "ui_write"
    UI_GENERIC = "ui_generic"
    OTHER = "other"


# The four ``x-*`` keys below are JSON-Schema extensions stamped at the schema
# root, which is what carries them to the *client*: AG-UI has no native concept
# for any of them and passes unknown keys through verbatim.

# This tool mutates. Read client-side to gate execution behind a confirmation.
X_DESTRUCTIVE_KEY = "x-destructive"

# The tool's category, so a frontend can group or filter without a side channel.
X_CATEGORY_KEY = "x-category"

# A confirmation prompt shown instead of a generic "Run <tool>?".
X_CONFIRM_KEY = "x-confirm"

# A short label shown on the tool-call card instead of the raw tool name.
X_SUMMARY_KEY = "x-summary"

# The same "this tool mutates" signal, but on ``ToolDefinition.metadata`` rather
# than the schema, because its audience is *server-side*: ``ToolGuard`` reads it
# at ``prepare_tools`` time for tools whose destructiveness the ``@tool``
# registry does not know, such as a drf-mcp tool whose ``readOnlyHint`` the
# bridge maps onto this key.
DESTRUCTIVE_METADATA_KEY = "django_pydantic_agent.destructive"


# The ``STORAGES`` alias attachment bytes are written through, when a project
# names one. Agent uploads are user-supplied files a project usually wants in a
# private bucket, and without this the only way to move them is to change the
# global default and every other ``FileField`` with it.
ATTACHMENT_STORAGE_ALIAS = "django_pydantic_agent_attachments"


__all__ = [
    "ATTACHMENT_STORAGE_ALIAS",
    "DESTRUCTIVE_METADATA_KEY",
    "X_CATEGORY_KEY",
    "X_CONFIRM_KEY",
    "X_DESTRUCTIVE_KEY",
    "X_SUMMARY_KEY",
    "ToolCategory",
]
