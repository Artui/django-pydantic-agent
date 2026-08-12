"""``SpecSource`` — the structural shape a spec registry satisfies."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SpecSource(Protocol):
    """Anything that can hand back a ``name -> spec`` mapping.

    ``djangorestframework-services``' ``SpecRegistry`` (0.27+) is the intended
    implementation: a project exposing the same specs over more than one
    transport declares them there once, and each transport reads that source.

    It is matched structurally rather than imported, because this substrate
    depends on ``pydantic-ai-slim`` alone and drf-services arrives only with the
    optional ``[spec-tools]`` extra. Naming ``SpecRegistry`` in a signature would
    force that dependency on every install or bury the type behind a lazy import
    where a signature cannot reach it.

    A plain ``dict`` is *not* a ``SpecSource`` — it has no ``specs()`` — which is
    what lets a caller accept either and tell them apart.
    """

    def specs(self) -> dict[str, Any]:
        """The ``name -> spec`` mapping this source declares."""
        ...


__all__ = ["SpecSource"]
