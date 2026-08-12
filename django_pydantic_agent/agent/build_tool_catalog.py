from __future__ import annotations

import inspect
import re
from typing import Any

from django_pydantic_agent.registry.tool_registry import ToolRegistry


def build_tool_catalog(
    registry: ToolRegistry,
    *,
    drf_mcp_server: Any = None,
    service_specs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The agent's server-tool catalog for the frontend to label tool-call cards.

    Server-side tools execute server-side, so their JSON Schema never reaches the
    browser and a web component cannot read an ``x-summary`` off it. This catalog
    is that channel: the component fetches it and maps tool name to label.

    Each entry is ``{"name", "summary", "description"?}``. ``summary`` is always
    present, resolved from ``@tool(summary=...)`` for registry tools and from
    ``display_name`` then ``title`` for drf-mcp ones, falling back to a
    prettified name. ``description`` is carried through when the source has one.

    **The catalog covers these sources and no others.** A tool attached through
    a transport's ``capabilities=`` / ``toolsets=`` is not listed and its card
    falls back to a prettified name — a degraded label, not a broken call.
    Enumerating those is not possible here: pydantic-ai exposes tool names only
    through ``AbstractToolset.get_tools``, which is async and needs a
    ``RunContext``, while this runs at configuration time with no run in sight.
    Route spec tools through a transport's ``service_specs=`` to keep labels.

    Args:
        registry: The ``@tool`` registry, listed first; it wins name collisions.
        drf_mcp_server: A drf-mcp server whose registered tools are appended.
        service_specs: A ``name -> spec`` mapping whose tools are appended, each
            described by its service or selector callable's docstring.

    Returns:
        One entry per tool, in that source order.
    """
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for binding in registry:
        spec = binding.spec
        catalog.append(_entry(spec.name, spec.summary, spec.description))
        seen.add(spec.name)
    if drf_mcp_server is not None:
        for binding in drf_mcp_server.tools.all():
            if binding.name in seen:
                continue
            summary = getattr(binding, "display_name", None) or getattr(binding, "title", None)
            description = getattr(binding, "display_description", None) or binding.description
            catalog.append(_entry(binding.name, summary, description))
            seen.add(binding.name)
    if service_specs is not None:
        for name, spec in service_specs.items():
            if name in seen:
                continue
            catalog.append(_entry(name, None, _spec_description(spec)))
            seen.add(name)
    return catalog


def _spec_description(spec: Any) -> str | None:
    """A spec tool's blurb: the docstring of its service / selector callable.

    Read through ``getattr`` rather than ``isinstance`` so this never imports the
    drf-services spec classes; the ``[spec-tools]`` extra stays optional.
    """
    callable_ = getattr(spec, "service", None) or getattr(spec, "selector", None)
    return inspect.getdoc(callable_) if callable_ is not None else None


def _entry(name: str, summary: str | None, description: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "summary": summary or _prettify(name)}
    if description:
        entry["description"] = description
    return entry


def _prettify(name: str) -> str:
    """Fallback label from a tool name: ``query_model`` → ``"Query model"``."""
    text = " ".join(word for word in re.split(r"[_\-\s]+", name) if word)
    return text[:1].upper() + text[1:]


__all__ = ["build_tool_catalog"]
