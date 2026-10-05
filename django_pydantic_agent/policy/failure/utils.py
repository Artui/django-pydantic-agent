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
    that adds nothing to it. Every pydantic-ai path that handles a
    ``ToolFailed`` is an ``isinstance`` check or an ``except ToolFailed`` clause
    and reads only ``message``, so this one is handled identically; the
    constructor is the parent's, so it pickles as ``cls(message)`` exactly as a
    ``ToolFailed`` does. Code mode's sandbox sees it as a plain ``Exception``
    carrying the message, as it sees any ``ToolFailed``.
    """


__all__ = ["PolicyToolFailed"]
