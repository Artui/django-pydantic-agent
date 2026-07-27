"""``SpecSource`` — the structural shape a spec registry satisfies."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SpecSource(Protocol):
    """Anything that can hand back a ``name -> spec`` mapping.

    ``djangorestframework-services``' ``SpecRegistry`` (0.27+) is the intended
    implementation: a project exposing the same specs over more than one
    transport declares them once there, and each transport reads that one
    source instead of enumerating the list again.

    It is matched **structurally rather than imported**, deliberately. This
    substrate depends on ``pydantic-ai-slim`` alone — drf-services arrives only
    through the optional ``[spec-tools]`` extra — so naming ``SpecRegistry`` in
    a signature would either force a hard dependency on every install (including
    projects whose tools are plain ``@tool`` functions) or bury the type behind
    a lazy import where it can't appear in a signature at all. Duck typing costs
    one ``specs()`` call and keeps the core dependency-free. drf-services applies
    the same reasoning to ``SelectorSpec.filter_set``, which is duck-typed so its
    ``types/`` package imports nothing.

    A plain ``dict`` is *not* a ``SpecSource`` — it has no ``specs()`` — which is
    exactly what lets a caller accept either and tell them apart.
    """

    def specs(self) -> dict[str, Any]:
        """The ``name -> spec`` mapping this source declares."""
        ...


__all__ = ["SpecSource"]
