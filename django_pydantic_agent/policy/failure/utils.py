"""Internals the failure policy shares with the audit capability."""

from __future__ import annotations

from pydantic_ai.exceptions import ToolFailed


class PolicyToolFailed(ToolFailed):
    """The ``ToolFailed`` that ``ToolFailurePolicy`` raises in place of a tool's
    exception, always ``from`` that exception.

    It exists so ``AuditCapability`` can tell this translation apart from every
    other ``ToolFailed``. A tool's own, a toolset's (a spec tool's timeout, an
    MCP tool's error) and another capability's all reach the audit wrapper as a
    plain ``ToolFailed`` under code mode, whose nested tool manager does not
    convert them, and each one's message was written on purpose. Only this one
    stands in for an exception whose text the model was not shown, so only this
    one is described by its cause.

    A subclass rather than an attribute set on a plain ``ToolFailed``, and one
    that adds nothing to it. pydantic-ai's control flow treats it as a
    ``ToolFailed``: every path that handles one is an ``isinstance`` check or an
    ``except ToolFailed`` clause and reads only ``message``. The constructor is
    the parent's, so it pickles as ``cls(message)`` exactly as a ``ToolFailed``
    does, and code mode's sandbox sees it as a plain ``Exception`` carrying the
    message, as it sees any ``ToolFailed``.

    **What differs is its name, and two things see it.** A trace records the
    subclass as the exception type: from pydantic-ai 2.54 the instrumentation
    capability is outermost on ``wrap_tool_execute``, so a failed tool span's
    ``exception.type`` reads
    ``django_pydantic_agent.policy.failure.utils.PolicyToolFailed`` where it
    would otherwise read ``pydantic_ai.exceptions.ToolFailed`` (before 2.54 it
    names the tool's own exception, which that wrapper saw first). And it never
    compares equal to a plain ``ToolFailed`` with the same message, in either
    direction: ``ToolFailed.__eq__`` requires the other side to be an instance
    of its own class, and Python tries the subclass's reflected comparison
    first.
    """


__all__ = ["PolicyToolFailed"]
