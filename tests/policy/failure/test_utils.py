from __future__ import annotations

import pickle

import pytest
from pydantic_ai.exceptions import ToolFailed

from django_pydantic_agent.policy.failure.utils import PolicyToolFailed

# What the policy raises is observed from real runs in
# ``test_tool_failure_policy.py``. These hold the two claims the subclass's
# docstring makes about the class itself.


def test_it_never_equals_a_plain_tool_failed_with_the_same_message() -> None:
    """In either direction, while each class still compares by message.

    ``ToolFailed.__eq__`` requires the other side to be an instance of its own
    class, and Python tries the subclass's reflected comparison first, so the
    plain one on the left is refused by the subclass's ``__eq__`` too.
    """
    assert ToolFailed("m") == ToolFailed("m")
    assert PolicyToolFailed("m") == PolicyToolFailed("m")
    assert PolicyToolFailed("m") != ToolFailed("m")
    assert ToolFailed("m") != PolicyToolFailed("m")


@pytest.mark.parametrize("cls", [ToolFailed, PolicyToolFailed])
def test_it_pickles_as_cls_message_exactly_as_a_tool_failed_does(
    cls: type[ToolFailed],
) -> None:
    """The constructor is the parent's, so the copy is rebuilt as ``cls(message)``."""
    original = cls("The boom tool failed.")

    copy = pickle.loads(pickle.dumps(original))

    assert original.__reduce__()[:2] == (cls, ("The boom tool failed.",))
    assert type(copy) is cls
    assert copy == original
