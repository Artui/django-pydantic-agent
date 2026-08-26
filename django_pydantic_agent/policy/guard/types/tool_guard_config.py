from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolGuardConfig:
    """Resolved policy for the server-side tool-approval gate.

    Turns the [`ToolGuard`][django_pydantic_agent.ToolGuard]
    capability on and tunes which tools it flips to require human approval. Off
    by default, so the gate never surprises a project that has not opted in.

    When enabled, a tool is gated if it is destructive or its name is in
    ``require_approval``; ``exempt`` overrides both. A tool counts as destructive
    when it is a registry ``@tool(destructive=True)``, or when its definition
    says so — the drf-mcp bridge's metadata key, an MCP ``readOnlyHint`` of
    ``False`` (a drf-services ``ServiceSpec`` through ``SpecToolset``, among
    others), or an ``x-destructive`` stamp at the root of its parameter schema.

    **Nothing else is gated.** A tool from a source that declares none of those
    is invisible to the gate however dangerous it is, and ``require_approval``
    is the only answer for it. Off by default, so on stock settings a
    server-side destructive tool runs with no approval interrupt at all.
    """

    enabled: bool = False
    """Whether the ``ToolGuard`` capability is composed into the agent."""

    exempt: frozenset[str] = field(default_factory=frozenset)
    """Tool names **never** gated, even if flagged destructive: the escape hatch
    for a mutation a project has decided is safe to auto-run."""

    require_approval: frozenset[str] = field(default_factory=frozenset)
    """Tool names **always** gated, even if not flagged destructive, for a read
    tool a project treats as sensitive. ``exempt`` wins on a name in both."""


__all__ = ["ToolGuardConfig"]
