"""``AgentDeps`` — the per-run dependency record handed to the agent."""

from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from pydantic_ai.ui import StateHandler

from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_deps import AgentDeps


class _Doc(BaseModel):
    body: str = ""


def test_carries_the_acting_user() -> None:
    user = SimpleNamespace(name="alice")
    assert AgentDeps(user=user).user is user


def test_the_acting_user_has_to_be_stated() -> None:
    """Pydantic-AI does not enforce ``deps=`` and never validates what it gets,
    so a default here was the difference between an unauthenticated run and a
    run nobody decided the identity of.

    Registry tools do not fail closed the way a spec tool does — they run with no
    user context at all and the answer looks ordinary — so the only place to
    catch it is construction.
    """
    with pytest.raises(TypeError):
        AgentDeps()  # type: ignore[call-arg]


def test_an_unauthenticated_run_says_so_in_one_word() -> None:
    """Nothing is forbidden, only made explicit: the anonymous run is still one
    argument away."""
    deps = AgentDeps(user=None)
    assert (deps.user, deps.state, deps.progress) == (None, None, None)


def test_the_docstring_only_names_settings_that_exist() -> None:
    """It sent readers to ``AgentConfig.deps_factory``, which raises ``TypeError``
    — the parameter is real but lives on the transport, one layer up."""
    named = set(re.findall(r"AgentConfig\.(\w+)", AgentDeps.__doc__ or ""))
    fields = {f.name for f in dataclasses.fields(AgentConfig)}

    assert named <= fields, f"AgentDeps names non-existent AgentConfig fields: {named - fields}"


def test_carries_a_progress_sink_for_the_run() -> None:
    """A spec's ``progress(...)`` calls are read off ``ctx.deps.progress``. With
    no field to read they resolved to drf-services' no-op and vanished, and a
    project could not fix it by subclassing without also owning the extractor."""
    reports: list[tuple[int, int | None]] = []

    def sink(progress: int, *, total: int | None = None, **_: object) -> None:
        reports.append((progress, total))

    deps = AgentDeps(user=None, progress=sink)
    deps.progress(1, total=2)  # type: ignore[misc]

    assert reports == [(1, 2)]


class TestStateHandlerConformance:
    """Pydantic-AI decides whether a run receives ``RunAgentInput.state`` by
    ``isinstance(deps, StateHandler)``. These pin the two things that check
    needs, so shared-state support doesn't need the deps redesigned."""

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(AgentDeps(user=None), StateHandler)

    def test_state_is_assignable(self) -> None:
        """The adapter assigns ``deps.state = state`` directly — it does *not*
        use ``dataclasses.replace``, despite what the protocol's own comment
        says. So this record cannot be frozen."""
        deps = AgentDeps(user=None)
        deps.state = {"document": "hello"}

        assert deps.state == {"document": "hello"}

    def test_is_a_dataclass(self) -> None:
        """The protocol requires ``__dataclass_fields__``."""
        assert dataclasses.is_dataclass(AgentDeps)

    def test_a_pydantic_model_seeds_validation(self) -> None:
        """The adapter validates incoming state against ``type(deps.state)``, so
        seeding an instance is how a project opts into a typed state model."""
        deps = AgentDeps(user=None, state=_Doc())
        assert isinstance(deps.state, BaseModel)
