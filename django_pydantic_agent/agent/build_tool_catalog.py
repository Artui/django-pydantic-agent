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

    Server-side tools (the ``@tool`` registry, and drf-mcp tools when a
    ``drf_mcp_server`` is passed) execute server-side, so their
    JSON Schema never reaches the browser — the web component can't read an
    ``x-summary`` off it. This catalog is the channel for those labels: the
    component fetches it via ``data-tools-url`` and maps tool name → label.

    Each entry is ``{"name", "summary", "description"?}``. ``summary`` is always
    present, resolved from the single source of truth with a fallback chain:

    - registry tools → ``@tool(summary=…)`` → a prettified name;
    - drf-mcp tools → ``display_name`` → ``title`` → a prettified name.

    ``description`` (a longer blurb for tooltips) is included when available
    (``ToolSpec.description`` / drf-mcp ``display_description`` → ``description``).
    Registry tools win on name collisions.

    ⚠ **The catalog covers the sources named here, and no others.** Tools
    reaching the agent through a transport's ``capabilities=`` / ``toolsets=``
    — an arbitrary ``AbstractToolset`` a project attaches directly — are **not**
    listed, and their cards fall back to a prettified name in the frontend.

    ⛔ **That boundary is deliberate, and enumerating them is not a missing
    feature.** Pydantic-AI's enumeration is ``AbstractToolset.get_tools``, which
    is ``async`` and takes a ``RunContext``; this function runs at configuration
    time, from a view, with no run in sight. No common surface exposes tool
    names before a run, so any attempt here would work for the toolsets we
    happen to recognise and silently skip the rest — a catalog that *looks*
    complete and is not, which is the exact failure mode this stack keeps
    fixing everywhere else.

    ⭐ **Prefer routing spec tools through the source that is covered.** A
    ``SpecToolset`` / ``SpecCapability`` handed to django-ag-ui's
    ``service_specs=`` (0.30+) is attached as itself *and* enumerated here, so
    the powerful form keeps its labels; only a toolset with no such route stays
    unlabelled.

    ⚠ Unlabelled is a **degraded label, not a broken call** — the frontend
    prettifies the tool name and the tool works normally. That is why the
    boundary is documented rather than closed with a partial guess.
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

    Read via ``getattr`` (not isinstance) so this never imports the drf-services
    spec classes — the ``[spec-tools]`` extra need not be installed to fetch the
    catalog when ``SERVICE_SPECS`` is unset.
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
