"""Internals of the failure policy.

Here rather than private to ``tool_failure_policy.py`` because a trace records
the exception's qualified name, so this module's path is part of what a failed
tool span says.
"""

from __future__ import annotations

from pydantic_ai.exceptions import ToolFailed


class PolicyToolFailed(ToolFailed):
    """The ``ToolFailed`` that ``ToolFailurePolicy`` raises in place of a tool's
    exception, always ``from`` that exception.

    The audit record does not depend on it. ``AuditCapability``'s error hook
    runs before the policy's and keeps the tool's own exception, so the record
    never sees this one.

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
