"""``ToolGuard`` — flip destructive tools to require human-in-the-loop approval."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition

from django_pydantic_agent.constants import DESTRUCTIVE_METADATA_KEY, X_DESTRUCTIVE_KEY
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

    Destructiveness is read from every vocabulary a toolset might declare it in,
    so one hook covers every tool the agent sees wherever it came from:

    - **Registry** ``@tool(destructive=True)``, collected at construction: the
      flag lives on the spec and never reaches pydantic-ai, which sees a bare
      callable, so the capability reads it directly.
    - **drf-mcp bridged tools**, through the
      [`DESTRUCTIVE_METADATA_KEY`][django_pydantic_agent.DESTRUCTIVE_METADATA_KEY] the
      bridge stamps into ``ToolDefinition.metadata``.
    - **MCP tool annotations** — ``metadata["annotations"]["readOnlyHint"] is
      False``. A toolset that speaks MCP's own vocabulary rather than this
      package's key declares a mutation this way, ``SpecToolset`` over a
      drf-services ``ServiceSpec`` among them. Without this the *same* spec was
      gated when it arrived over the drf-mcp bridge and ungated when it was
      attached in process, so a transport swap silently removed the gate.
    - **The ``x-destructive`` schema stamp** at the root of
      ``parameters_json_schema``, which is what
      [`build_input_schema`][django_pydantic_agent.build_input_schema] writes.
      That key is documented as the client-side signal and had no server-side
      reader; a project deriving a schema with the package's own helper and
      attaching the tool through ``toolsets=`` got no gate from it.
    - **Project overrides**: ``require_approval`` force-gates a name, ``exempt``
      un-gates one, and ``exempt`` wins.

    A hint has to *say* the tool mutates. A missing ``readOnlyHint``, an absent
    stamp or metadata of another shape entirely leaves the tool alone — silence
    is not a claim, and ``require_approval`` is the answer for a tool whose
    source declares nothing.

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
        return _declares_a_mutation(tool_def)


def _declares_a_mutation(tool_def: ToolDefinition) -> bool:
    """Whether the tool itself says it mutates, in any of the three vocabularies.

    Read in cost order, cheapest first. Each is checked independently: a toolset
    speaks one of them, and which one is a fact about its author rather than
    about the tool.
    """
    metadata = tool_def.metadata or {}
    if metadata.get(DESTRUCTIVE_METADATA_KEY):
        return True
    # MCP's own name for the same fact. Keyed on ``readOnlyHint`` rather than
    # ``destructiveHint`` for the reason the drf-mcp bridge is: the latter is
    # omitted on read-only tools, so only the former distinguishes "reads" from
    # "said nothing". ``Mapping`` guard because metadata is free-form.
    annotations = metadata.get("annotations")
    if isinstance(annotations, Mapping) and annotations.get("readOnlyHint") is False:
        return True
    return bool((tool_def.parameters_json_schema or {}).get(X_DESTRUCTIVE_KEY))


__all__ = ["ToolGuard"]
