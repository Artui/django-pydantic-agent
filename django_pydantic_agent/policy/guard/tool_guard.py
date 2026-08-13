"""``ToolGuard`` — flip destructive tools to require human-in-the-loop approval."""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.constants import DESTRUCTIVE_METADATA_KEY
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_pydantic_agent.registry.tool_registry import ToolRegistry


class ToolGuard(AbstractCapability[Any]):
    """Gates destructive server-side tools behind the AG-UI approval loop.

    pydantic-ai supplies the mechanism — a tool whose definition is
    ``kind="unapproved"`` defers to an interrupt the client approves or denies —
    and this supplies the policy, flipping a plain ``function`` tool to
    ``unapproved`` at ``prepare_tools`` time when it is destructive. Server-side
    tools thereby get the confirmation gate the web component already applies to
    client-registered ones.

    Destructiveness is read from three sources, so one hook covers every tool the
    agent sees wherever it came from:

    - **Registry** ``@tool(destructive=True)``, collected at construction: the
      flag lives on the spec and never reaches pydantic-ai, which sees a bare
      callable, so the capability reads it directly.
    - **drf-mcp bridged tools**, through the
      [`DESTRUCTIVE_METADATA_KEY`][django_pydantic_agent.DESTRUCTIVE_METADATA_KEY] the
      bridge stamps into ``ToolDefinition.metadata``.
    - **Project overrides**: ``require_approval`` force-gates a name, ``exempt``
      un-gates one, and ``exempt`` wins.

    Only ``kind="function"`` tools are flipped — an ``external`` tool is already
    gated client-side, and an ``output`` tool is not executed.

    The guard touches only ``prepare_tools``, so it is orthogonal to
    [`AuditCapability`][django_pydantic_agent.AuditCapability]
    and audit still records the tool when an approved call finally runs.
    """

    def __init__(self, registry: ToolRegistry, *, config: ToolGuardConfig) -> None:
        self._destructive_names = frozenset(
            binding.spec.name for binding in registry if binding.spec.destructive
        )
        self._exempt = config.exempt
        self._require_approval = config.require_approval

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        return [
            dataclasses.replace(tool_def, kind="unapproved")
            if tool_def.kind == "function" and self._requires_approval(tool_def)
            else tool_def
            for tool_def in tool_defs
        ]

    def _requires_approval(self, tool_def: ToolDefinition) -> bool:
        """Whether ``tool_def`` should be gated, applying the policy precedence."""
        if tool_def.name in self._exempt:
            return False
        if tool_def.name in self._require_approval:
            return True
        if tool_def.name in self._destructive_names:
            return True
        metadata = tool_def.metadata or {}
        return bool(metadata.get(DESTRUCTIVE_METADATA_KEY))


__all__ = ["ToolGuard"]
