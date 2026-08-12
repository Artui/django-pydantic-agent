from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django_pydantic_agent.constants import ToolCategory


@dataclass(frozen=True)
class ToolSpec:
    """Canonical declaration of a server-side tool.

    Bundles the callable with the metadata the registry needs to expose it to a
    Pydantic-AI agent and to a frontend.
    """

    name: str
    """Stable identifier exposed to the agent, unique within a
    :class:`~django_pydantic_agent.registry.tool_registry.ToolRegistry`."""

    fn: Callable[..., Any]
    """The callable implementing the tool. Its parameters must be typed; the
    registry derives a JSON Schema from the signature."""

    description: str
    """Summary shown to the agent. Most clients display the first line."""

    destructive: bool = False
    """Whether calling this tool may mutate state. Stamped as
    ``x-destructive`` so a frontend can gate it behind a confirmation step."""

    category: ToolCategory = ToolCategory.OTHER
    """Coarse capability grouping, stamped as ``x-category``."""

    confirm: str | None = None
    """A confirmation prompt for a destructive tool, stamped as ``x-confirm``,
    shown instead of a generic "Run <tool>?"."""

    summary: str | None = None
    """A short label, stamped as ``x-summary``, shown on the tool-call card
    instead of the raw tool name."""


__all__ = ["ToolSpec"]
