"""``AgentDeps`` — the per-run dependency record handed to the agent."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from pydantic import BaseModel
from pydantic_ai.ui import StateHandler

from django_pydantic_agent.agent.types.agent_deps import AgentDeps


class _Doc(BaseModel):
    body: str = ""


def test_defaults_to_an_anonymous_stateless_run() -> None:
    deps = AgentDeps()
    assert (deps.user, deps.state) == (None, None)


def test_carries_the_acting_user() -> None:
    user = SimpleNamespace(name="alice")
    assert AgentDeps(user=user).user is user


class TestStateHandlerConformance:
    """Pydantic-AI decides whether a run receives ``RunAgentInput.state`` by
    ``isinstance(deps, StateHandler)``. These pin the two things that check
    needs, so shared-state support doesn't need the deps redesigned."""

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(AgentDeps(), StateHandler)

    def test_state_is_assignable(self) -> None:
        """The adapter assigns ``deps.state = state`` directly — it does *not*
        use ``dataclasses.replace``, despite what the protocol's own comment
        says. So this record cannot be frozen."""
        deps = AgentDeps()
        deps.state = {"document": "hello"}

        assert deps.state == {"document": "hello"}

    def test_is_a_dataclass(self) -> None:
        """The protocol requires ``__dataclass_fields__``."""
        assert dataclasses.is_dataclass(AgentDeps)

    def test_a_pydantic_model_seeds_validation(self) -> None:
        """The adapter validates incoming state against ``type(deps.state)``, so
        seeding an instance is how a project opts into a typed state model."""
        deps = AgentDeps(state=_Doc())
        assert isinstance(deps.state, BaseModel)
