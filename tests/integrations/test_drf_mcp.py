from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from pydantic_ai import Agent, ModelRetry, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework_mcp import JsonRpcError, JsonRpcErrorCode, MCPServer, QueryParam
from rest_framework_mcp.schema import PAGED_QUERY_PARAM_SCOPE
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.integrations.build_spec_capability import build_spec_capability
from django_pydantic_agent.integrations.drf_mcp import DRFMCPToolset, _retry_message
from tests.integrations.drf_server import BOOKS, REFUSED_SPEC, server
from tests.integrations.drf_server_lookup import lookup_server
from tests.integrations.drf_specs_lookup import SPECS as LOOKUP_SPECS
from tests.integrations.drf_specs_lookup import Row


def _request() -> HttpRequest:
    request = RequestFactory().post("/agent/")
    request.user = AnonymousUser()  # type: ignore[attr-defined]
    return request


async def test_toolset_exposes_drf_tools_with_schemas() -> None:
    toolset = DRFMCPToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert "add" in tools
    tool_def = tools["add"].tool_def
    assert tool_def.name == "add"
    schema = tool_def.parameters_json_schema
    assert schema["type"] == "object"
    # Sourced from drf-mcp's own tools/list, so the merged inputSchema carries
    # the `additionalProperties` policy too; `add` defaults to REJECT.
    assert schema["additionalProperties"] is False
    # An in-process function rather than a deferred `external` call, so
    # pydantic-ai's run loop actually invokes our `call_tool`.
    assert tool_def.kind == "function"


@pytest.mark.django_db
async def test_agent_run_executes_drf_tool_in_process() -> None:
    # The real regression: drive a full agent run. With `kind="external"` the
    # tool was deferred to the client and never executed (the run stalled); as a
    # `function` tool Pydantic-AI runs it in-process and returns its result.
    toolset = DRFMCPToolset(server, _request())
    agent = Agent(TestModel(call_tools=["add"]), toolsets=[toolset])
    result = await agent.run("add two numbers")
    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "add"
    ]
    assert returns, "drf-mcp 'add' tool was deferred, not executed in-process"
    assert "result" in returns[0].content


@pytest.mark.django_db
async def test_agent_run_recovers_from_model_retry() -> None:
    # A ModelRetry (malformed arguments) must be fed back to the model to
    # self-correct, consuming one unit of the tool's retry budget — not abort
    # the run. The budget was previously pinned to 0, so the first retry died
    # with UnexpectedModelBehavior.
    def model_fn(messages: list, info: object) -> ModelResponse:
        last = messages[-1]
        if any(part.part_kind == "retry-prompt" for part in last.parts):
            return ModelResponse(parts=[ToolCallPart(tool_name="add", args={"a": 5, "b": 3})])
        if any(part.part_kind == "tool-return" for part in last.parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name="add", args={"a": "not_a_number", "b": 3})]
        )

    toolset = DRFMCPToolset(server, _request())
    agent = Agent(FunctionModel(model_fn), toolsets=[toolset])
    result = await agent.run("add two numbers")
    assert result.output == "done"


async def test_max_retries_default_and_override() -> None:
    toolset = DRFMCPToolset(server, _request())
    assert toolset.id == "drf-mcp"
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert tools["add"].max_retries == 1
    toolset = DRFMCPToolset(server, _request(), max_retries=3)
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert tools["add"].max_retries == 3


async def test_loads_all_pages_from_tools_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive the cursor loop: drf-mcp paginates tools/list, so the bridge must
    # follow `nextCursor` until it's exhausted.
    pages = [
        {"tools": [{"name": "p1", "inputSchema": {"type": "object"}}], "nextCursor": "c2"},
        {
            "tools": [{"name": "p2", "inputSchema": {"type": "object"}, "description": "two"}],
            "nextCursor": "c3",
        },
        {"tools": []},  # a trailing empty page exercises the zero-tools branch
    ]
    calls: list[str | None] = []

    def fake_list(cursor: str | None = None, **_kwargs: object) -> dict[str, object]:
        calls.append(cursor)
        return pages[len(calls) - 1]

    monkeypatch.setattr(server, "list_tools", fake_list)
    toolset = DRFMCPToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert {"p1", "p2"} <= set(tools)
    assert calls == [None, "c2", "c3"]

    # A second call is memoised — no further tools/list round-trips.
    await toolset.get_tools(None)  # type: ignore[arg-type]
    assert calls == [None, "c2", "c3"]


async def test_tools_list_error_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    error = JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "bad request")
    monkeypatch.setattr(server, "list_tools", lambda *_a, **_k: error)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(RuntimeError, match="drf-mcp tools/list failed"):
        await toolset.get_tools(None)  # type: ignore[arg-type]


@pytest.mark.django_db
async def test_toolset_invokes_drf_tool_as_acting_user() -> None:
    toolset = DRFMCPToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    result = await toolset.call_tool("add", {"a": 5, "b": 3}, None, tools["add"])
    assert result == {"result": 8}


async def test_an_unknown_tool_is_now_retryable_not_fatal() -> None:
    """**Changed by drf-mcp 0.24.0, and the change is upstream's.** An unknown
    tool used to arrive as `-32004` and this bridge killed the run; the MCP spec's
    own worked example puts it on `-32602`, indistinguishable from malformed
    arguments. Retrying is the deliberate choice, since both are faults in the
    request the model produced and both are things it can change.
    """
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry, match="Unknown tool"):
        await toolset.call_tool("nope", {}, None, None)


async def test_a_bad_name_retry_names_the_real_tools() -> None:
    """What makes retrying worth doing rather than merely survivable: a model
    that invented a name needs the real ones. Requires ``get_tools`` to have run
    — which, in a real agent run, it always has."""
    toolset = DRFMCPToolset(server, _request())
    await toolset.get_tools(None)  # type: ignore[arg-type]
    with pytest.raises(ModelRetry, match="Available tools:.*add"):
        await toolset.call_tool("nope", {}, None, None)


async def test_a_bad_name_retry_says_nothing_extra_when_the_cache_is_cold() -> None:
    """No ``get_tools`` yet, so there is no list to offer. The message must
    still be the server's own rather than an empty enumeration."""
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry) as caught:
        await toolset.call_tool("nope", {}, None, None)
    assert "Available tools" not in str(caught.value)


async def test_a_fault_the_model_cannot_rewrite_still_ends_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth, rate limits and internal faults are not retryable — nothing the
    model writes changes them, so the run stops rather than burning its budget."""

    async def denied(*_a: object, **_k: object) -> JsonRpcError:
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")

    toolset = DRFMCPToolset(server, _request())
    monkeypatch.setattr(server, "acall_tool", denied)
    with pytest.raises(RuntimeError, match="drf-mcp tool 'add'"):
        await toolset.call_tool("add", {}, None, None)


async def test_malformed_arguments_raise_model_retry_with_detail() -> None:
    # The serializer rejecting the arguments *shape* becomes ``ModelRetry``
    # carrying the field errors, so the model self-corrects instead of the run
    # dying with RUN_ERROR. drf-mcp answers it with an ``isError``
    # ``validation_error`` result, where it used JSON-RPC -32602 before 0.50;
    # the bridge retries either.
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry, match="Invalid arguments") as excinfo:
        await toolset.call_tool("add", {"a": "not_a_number", "b": 1}, None, None)
    # The per-field DRF detail rides in the retry text for the model.
    assert "valid integer" in str(excinfo.value)


async def test_invalid_params_error_raises_model_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Payload-level twin of the -32602 branch, which drf-mcp still takes for an
    # unknown tool and took for refused arguments before 0.50: on Python 3.11
    # the C tracer drops the bridge frame across drf-mcp's real executor hop,
    # leaving the branch uncovered there even though it runs.
    async def fake_call(name: str, arguments: object = None, **_kwargs: object) -> JsonRpcError:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "Invalid arguments")

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry, match="Invalid arguments"):
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)


async def test_service_validation_error_result_raises_model_retry() -> None:
    # drf-mcp 0.7+ returns service-raised validation as an ``isError`` tool
    # result; the bridge still maps it to a retry.
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry, match="must be even"):
        await toolset.call_tool("invalid", {"a": 1, "b": 2}, None, None)


async def test_service_error_result_raises_tool_failed() -> None:
    # A business-rule denial is a sentence the model reads and adapts to, and a
    # failed call to everything streaming the run. It was returned as the tool's
    # value, which pydantic-ai records as a success.
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ToolFailed) as excinfo:
        await toolset.call_tool("denied", {"a": 1, "b": 2}, None, None)
    # Nothing names a plain ``ServiceError``, so no suffix is invented for one.
    assert excinfo.value.message == "denied by policy"


async def test_a_refusal_carries_its_code() -> None:
    # drf-mcp serves an ``ActionUnavailable``'s code beside the sentence; the
    # bridge keeps it in the only channel ``ToolFailed`` has, in the form the
    # spec-tools route writes.
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ToolFailed) as excinfo:
        await toolset.call_tool("refused", {}, None, None)
    assert excinfo.value.message == "The books are closed. (code: books_closed)"


async def test_a_chain_refusal_names_the_code_and_the_step() -> None:
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ToolFailed) as excinfo:
        await toolset.call_tool("refused_chain", {}, None, None)
    assert excinfo.value.message == "The books are closed. (code: books_closed, step: void)"


async def test_a_refusal_reads_the_same_through_both_bridges() -> None:
    """The same refused spec, called in process and over the drf-mcp bridge.

    A consumer can expose one spec either way, and the model is taught one
    wording for a refusal. Asserted against the other package's real output
    rather than a copy of its format, so a change on either side fails here.
    """
    capability = build_spec_capability({"refused": REFUSED_SPEC})
    ctx = RunContext(deps=AgentDeps(user=AnonymousUser()), model=TestModel(), usage=RunUsage())
    with pytest.raises(ToolFailed) as in_process:
        await capability.get_toolset().call_tool("refused", {}, ctx, None)
    with pytest.raises(ToolFailed) as bridged:
        await DRFMCPToolset(server, _request()).call_tool("refused", {}, None, None)
    assert bridged.value.message == in_process.value.message


@pytest.mark.django_db
async def test_agent_run_records_a_refusal_as_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The regression one hop out: a transport streaming the run reads
    # ``outcome``, and a refusal returned as a value arrived as ``"success"``.
    # The bridge offers a tool only while its condition is met, so a run meets
    # the refusal when the condition flips between offering the tool and the
    # call: here the books close while the model is choosing.
    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        if any(part.part_kind == "tool-return" for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        monkeypatch.setitem(BOOKS, "open", False)
        return ModelResponse(parts=[ToolCallPart(tool_name="refused", args={})])

    monkeypatch.setitem(BOOKS, "open", True)
    agent = Agent(FunctionModel(model_fn), toolsets=[DRFMCPToolset(server, _request())])
    result = await agent.run("close the books")
    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "refused"
    ]
    assert [(part.outcome, part.content) for part in returns] == [
        ("failed", "The books are closed. (code: books_closed)")
    ]


# ---------- availability, per step ----------

_MISSING = [
    "- These operations exist but cannot be performed right now, so they are not among your "
    "tools. If the user asks for one, say it is unavailable at the moment and give the reason "
    "listed for it, rather than guessing why:",
    "  - `refused`: The books are closed.",
    "  - `refused_chain`: The books are closed.",
]


async def test_a_tool_whose_condition_is_unmet_is_not_offered() -> None:
    # Closed books refuse every call of both tools, whatever the arguments, so
    # offering them only invites that refusal.
    tools = await DRFMCPToolset(server, _request()).get_tools(None)  # type: ignore[arg-type]
    assert "add" in tools
    assert not {"refused", "refused_chain"} & set(tools)


async def test_a_tool_that_becomes_available_is_offered_without_listing_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Listed once, unavailable tools included, so the definition is there when
    # the books open; only availability is asked again.
    listings: list[object] = []
    list_tools = server.list_tools

    def counting_list(*args: object, **kwargs: object) -> object:
        listings.append(kwargs.get("include_unavailable"))
        return list_tools(*args, **kwargs)

    monkeypatch.setattr(server, "list_tools", counting_list)
    toolset = DRFMCPToolset(server, _request())
    closed = await toolset.get_tools(None)  # type: ignore[arg-type]
    monkeypatch.setitem(BOOKS, "open", True)
    opened = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert "refused" not in closed
    assert opened["refused"].tool_def.name == "refused"
    assert listings == [True]


async def test_instructions_name_each_missing_tool_with_its_reason() -> None:
    instructions = await DRFMCPToolset(server, _request()).get_instructions(None)
    assert instructions is not None
    assert instructions.splitlines() == _MISSING


async def test_no_instructions_when_nothing_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(BOOKS, "open", True)
    assert await DRFMCPToolset(server, _request()).get_instructions(None) is None


async def test_a_name_the_registry_claimed_is_never_explained() -> None:
    # The registry's tool of that name is the one offered, so saying it cannot
    # run would be about a tool the model never sees.
    toolset = DRFMCPToolset(server, _request(), exclude_names=frozenset({"refused"}))
    instructions = await toolset.get_instructions(None)
    assert instructions is not None
    assert instructions.splitlines() == [_MISSING[0], _MISSING[2]]


async def test_a_missing_tool_reads_the_same_through_both_bridges() -> None:
    """The same unavailable spec, offered in process and over the drf-mcp bridge.

    Asserted against the other package's real instructions rather than a copy of
    its wording, so a change on either side fails here. The spec-tools route
    puts its conventions first and the missing tools last; the bridge has no
    conventions of its own, so its whole block is that last part.
    """
    capability = build_spec_capability({"refused": REFUSED_SPEC})
    ctx = RunContext(deps=AgentDeps(user=AnonymousUser()), model=TestModel(), usage=RunUsage())
    in_process = await capability.get_toolset().get_instructions(ctx)
    bridged = await DRFMCPToolset(server, _request()).get_instructions(None)
    assert in_process is not None
    assert bridged is not None
    heading, refused, _chain = bridged.splitlines()
    assert in_process.endswith(f"\n{heading}\n{refused}")


@pytest.mark.django_db
async def test_each_step_offers_and_explains_what_is_available_then(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Driven through a real run, so what is asserted is what pydantic-ai handed
    # the model: the books close after the first step, and the second step
    # neither offers the tool nor leaves the model guessing why.
    seen: list[tuple[bool, str | None]] = []

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        offered = "refused" in {tool.name for tool in info.function_tools}
        seen.append((offered, info.instructions))
        if len(seen) == 1:
            monkeypatch.setitem(BOOKS, "open", False)
            return ModelResponse(parts=[ToolCallPart(tool_name="add", args={"a": 1, "b": 2})])
        return ModelResponse(parts=[TextPart("done")])

    monkeypatch.setitem(BOOKS, "open", True)
    agent = Agent(FunctionModel(model_fn), toolsets=[DRFMCPToolset(server, _request())])
    await agent.run("close the books")
    (first_offered, first_said), (second_offered, second_said) = seen
    assert (first_offered, first_said) == (True, None)
    assert second_offered is False
    assert second_said is not None
    assert "\n".join(_MISSING) in second_said


async def test_input_required_result_raises_model_retry() -> None:
    # A request for more input is "call again with these", the retry channel's
    # meaning, named the way the spec-tools route names it.
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry) as excinfo:
        await toolset.call_tool("needs_input", {}, None, None)
    assert excinfo.value.message == (
        "Say why the books are being closed. Call this tool again, additionally supplying: `reason`."
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            {"type": "input_required", "message": "Say why.", "requestedInput": {"a": {}, "b": {}}},
            "Say why. Call this tool again, additionally supplying: `a`, `b`.",
        ),
        # No schema to name: the message alone, not an empty list of names.
        ({"type": "input_required", "message": "Say why."}, "Say why."),
        ({"type": "input_required", "message": "Say why.", "requestedInput": {}}, "Say why."),
    ],
)
async def test_input_required_payload_raises_model_retry(
    monkeypatch: pytest.MonkeyPatch, error: dict[str, object], expected: str
) -> None:
    # Payload-level twin of the test above, for the Python 3.11 tracer reason.
    _serve_error(monkeypatch, error)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry) as excinfo:
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)
    assert excinfo.value.message == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            {"type": "not_found", "message": "add: no matching instance found"},
            "add: no matching instance found",
        ),
        (
            {"type": "service_error", "message": "Closed.", "code": "books_closed"},
            "Closed. (code: books_closed)",
        ),
        (
            {"type": "service_error", "message": "Closed.", "failedStep": "void"},
            "Closed. (step: void)",
        ),
        (
            {
                "type": "service_error",
                "message": "Closed.",
                "code": "books_closed",
                "failedStep": "void",
            },
            "Closed. (code: books_closed, step: void)",
        ),
        # A payload with no message still fails the call with something to say.
        ({"type": "timeout"}, "tool error"),
    ],
)
async def test_other_error_payloads_raise_tool_failed(
    monkeypatch: pytest.MonkeyPatch, error: dict[str, object], expected: str
) -> None:
    # Payload-level twin of the integration tests above, for the same tracer
    # reason, and the one place each suffix combination is pinned.
    _serve_error(monkeypatch, error)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ToolFailed) as excinfo:
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)
    assert excinfo.value.message == expected


def _serve_error(monkeypatch: pytest.MonkeyPatch, error: dict[str, object]) -> None:
    """Make ``acall_tool`` answer with ``error``, encoded as drf-mcp encodes one."""
    import json as json_module

    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        text = json_module.dumps({"error": error})
        return {"isError": True, "content": [{"type": "text", "text": text}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)


async def test_validation_error_payload_raises_model_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Payload-level twin of the integration test above, for the same Python 3.11
    # tracer reason: the branch runs there but is not recorded.
    import json as json_module

    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        payload = {
            "error": {"type": "validation_error", "message": "bad", "detail": {"a": ["nope"]}}
        }
        return {"isError": True, "content": [{"type": "text", "text": json_module.dumps(payload)}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ModelRetry, match="bad.*nope"):
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)


async def test_unparseable_error_content_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        return {"isError": True, "content": [{"type": "text", "text": "not json"}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ToolFailed) as excinfo:
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)
    assert excinfo.value.message == "not json"


async def test_non_dict_error_payload_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        return {"isError": True, "content": [{"type": "text", "text": '{"error": "boom"}'}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DRFMCPToolset(server, _request())
    with pytest.raises(ToolFailed) as excinfo:
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)
    assert excinfo.value.message == "boom"


async def test_excluded_names_are_skipped_registry_wins() -> None:
    # A name collision with the @tool registry must not reach the
    # agent — pydantic-ai raises UserError for duplicate tool names.
    toolset = DRFMCPToolset(server, _request(), exclude_names=frozenset({"add"}))
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert "add" not in tools
    assert "denied" in tools


def test_retry_message_without_detail_is_the_bare_message() -> None:
    assert _retry_message("nope", None) == "nope"


def test_a_detail_the_message_already_names_is_dropped() -> None:
    # drf-mcp's missing-argument line names the argument its detail is keyed
    # by, and that detail's only reason is "required", which the line says.
    assert (
        _retry_message("Missing required argument(s): `pk`.", {"pk": ["This field is required."]})
        == "Missing required argument(s): `pk`."
    )


def test_a_name_counts_only_as_a_whole_token() -> None:
    """Holds the lookarounds: a name is matched as a token, never a substring.

    ``a`` sits inside ``arguments``, so a substring match would drop the only
    place the generic message's reason is written. A key ending in a non-word
    character still matches, which ``\\b`` would refuse.
    """
    detail = {"a": ["A valid integer is required."]}
    assert _retry_message("Invalid arguments", detail) == (
        'Invalid arguments: {"a": ["A valid integer is required."]}'
    )
    assert _retry_message("Missing required argument(s): `a`.", detail) == (
        "Missing required argument(s): `a`."
    )
    assert (
        _retry_message("Missing required argument(s): `tags[]`.", {"tags[]": ["Required."]})
        == "Missing required argument(s): `tags[]`."
    )


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param({"items": {"name": ["This field is required."]}}, id="dict"),
        pytest.param({"items": [{}, {"name": ["This field is required."]}]}, id="list"),
    ],
)
def test_a_nested_key_is_a_name_the_message_must_carry(detail: Any) -> None:
    # Every key counts, at any depth: naming ``items`` says nothing about which
    # of its fields was refused.
    assert _retry_message("Invalid `items`.", detail) == (f"Invalid `items`.: {json.dumps(detail)}")
    assert _retry_message("Invalid `name` in `items`.", detail) == "Invalid `name` in `items`."


def test_a_detail_the_message_names_only_in_part_is_kept_whole() -> None:
    # Every name, not any: the field the message leaves out is still in the
    # detail, and the detail is never cut down to it.
    detail = {"pk": ["This field is required."], "name": ["This field is required."]}
    assert _retry_message("Missing required argument(s): `pk`.", detail) == (
        f"Missing required argument(s): `pk`.: {json.dumps(detail)}"
    )
    assert (
        _retry_message("Missing required argument(s): `name`, `pk`.", detail)
        == "Missing required argument(s): `name`, `pk`."
    )


_UNEXPECTED = "Unexpected argument(s): 'notify_owner'."


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param({"non_field_errors": [_UNEXPECTED]}, id="non-field-key"),
        pytest.param([_UNEXPECTED], id="bare-list"),
        pytest.param(_UNEXPECTED, id="bare-string"),
    ],
)
def test_a_reason_under_no_field_counts_as_its_own_words(detail: Any) -> None:
    """Holds the non-field branch: DRF's non-field key is structure, not a name.

    Its strings sit under no field, so they are what the message has to quote,
    as a bare list or string's are; the key itself never counts as said.
    """
    assert _retry_message(_UNEXPECTED, detail) == _UNEXPECTED
    assert _retry_message("Invalid arguments", detail) == (
        f"Invalid arguments: {json.dumps(detail)}"
    )


@override_settings(REST_FRAMEWORK={"NON_FIELD_ERRORS_KEY": "__all__"})
def test_the_non_field_key_is_read_from_drf_settings() -> None:
    # A project renaming DRF's non-field key moves which key is structure: the
    # renamed key's strings must be quoted, and the default name is a field.
    assert _retry_message(_UNEXPECTED, {"__all__": [_UNEXPECTED]}) == _UNEXPECTED
    detail = {"non_field_errors": [_UNEXPECTED]}
    assert _retry_message(_UNEXPECTED, detail) == f"{_UNEXPECTED}: {json.dumps(detail)}"


class _SelectableRow(serializers.Serializer):
    """A row that reads its own ``?fields=id,name`` and refuses a name it lacks.

    No selection library behind it: the transport never reads the value, so the
    contract this bridge relies on is only that a serializer raising a
    ``ValidationError`` while rendering comes back as a result it can retry.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()

    def to_representation(self, instance: Any) -> Any:
        data = super().to_representation(instance)
        raw = self.context["request"].query_params.get("fields")
        if not raw:
            return data
        wanted = [name.strip() for name in raw.split(",")]
        for name in wanted:
            if name not in data:
                raise ValidationError(f"Unknown field `{name}`.", code="unknown_field")
        return {name: data[name] for name in wanted}


def _paged_selection_server() -> MCPServer:
    paged = MCPServer(name="paged")
    paged.register_selector_tool(
        name="list_rows",
        description="List rows.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            output_serializer=_SelectableRow,
            permission_classes=[AllowAny],
        ),
        paginate=True,
        query_params=[QueryParam("fields")],
    )
    return paged


async def test_a_selection_refused_while_rendering_is_one_retry_then_the_page() -> None:
    # drf-mcp answers a read-shaping value its output serializer refuses with an
    # ``isError`` ``validation_error`` result, and this route's self-correction
    # rests on the bridge turning that into ``ModelRetry``. The selection is the
    # likeliest wrong one on a paged tool: the envelope, which is the shape the
    # tool's result documents. Pinned here so a change to that result type fails
    # in this package rather than as a dead run in a consumer's.
    selections = iter(["items", "name"])

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        if any(part.part_kind == "tool-return" for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name="list_rows", args={"fields": next(selections)})]
        )

    toolset = DRFMCPToolset(_paged_selection_server(), _request())
    result = await Agent(FunctionModel(model_fn), toolsets=[toolset]).run("list the rows")

    retries = [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if part.part_kind == "retry-prompt"
    ]
    assert len(retries) == 1
    # The argument is named, the serializer is quoted in its own words, and the
    # model is told what the selection applies to on a paged tool. The detail,
    # keyed by ``fields``, is left off: the message names it and quotes its
    # reason, so appending it would repeat the sentence.
    assert retries[0] == (
        "`fields` was rejected while rendering the result: Unknown field `items`. "
        + PAGED_QUERY_PARAM_SCOPE
    )
    page = [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert page[0]["items"] == [{"name": "a"}, {"name": "b"}]


# Each tool reads its row through a selector taking ``pk`` with no default: the
# selector tool's own selector, and the service tool's target lookup. A call
# leaving ``pk`` out is corrected by sending it.
_LOOKUP_CALLS = [
    pytest.param("get_row", {}, {"pk": 1}, {"id": 1, "name": "first"}, id="selector"),
    pytest.param(
        "rename_row",
        {"name": "second"},
        {"pk": 1, "name": "second"},
        {"id": 1, "name": "second"},
        id="service-target-lookup",
    ),
]


@pytest.mark.parametrize("name", ["get_row", "rename_row"])
async def test_a_selector_parameter_without_a_default_is_required(name: str) -> None:
    # drf-mcp lists a selector parameter with no default, which the server does
    # not fill, in the tool's ``required``, and a service tool now advertises the
    # target lookup its row is resolved through. The bridge passes ``tools/list``
    # through verbatim, so this is what the model reads. Without either, the
    # schema called ``pk`` optional on ``get_row`` and left it out of
    # ``rename_row`` altogether, so nothing told a model to send the one argument
    # the call cannot run without.
    tools = await DRFMCPToolset(lookup_server, _request()).get_tools(None)
    schema = tools[name].tool_def.parameters_json_schema
    assert "pk" in schema["properties"]
    assert "pk" in schema.get("required", [])


@pytest.mark.parametrize(
    ("mcp_server", "name", "arguments", "message", "detail"),
    [
        pytest.param(
            lookup_server,
            "get_row",
            {},
            "Missing required argument(s): `pk`.",
            {"pk": ["This field is required."]},
            id="missing",
        ),
        pytest.param(
            server,
            "add",
            {"a": "not_a_number", "b": 1},
            "Invalid arguments",
            {"a": ["A valid integer is required."]},
            id="wrong-type",
        ),
    ],
)
async def test_refused_arguments_arrive_as_a_validation_error_result(
    mcp_server: MCPServer,
    name: str,
    arguments: dict[str, Any],
    message: str,
    detail: dict[str, Any],
) -> None:
    # The answer the retries here ride on, read off the real server rather than
    # a double: an ``isError`` result whose error is a ``validation_error``
    # keyed by the refused name, the branch of ``call_tool`` that raises
    # ``ModelRetry`` with the message and, where it adds something, the detail.
    # A missing selector argument raised ``TypeError`` before drf-mcp 0.50, and
    # a wrong type was JSON-RPC -32602. From drf-mcp 0.51 the missing one's
    # message names the argument, while a refused value keeps the generic one.
    result = await mcp_server.acall_tool(name, arguments, user=AnonymousUser())
    assert isinstance(result, dict), result
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == {
        "type": "validation_error",
        "message": message,
        "detail": detail,
    }


@pytest.mark.parametrize(("name", "omitted", "corrected", "row"), _LOOKUP_CALLS)
async def test_a_call_missing_a_selector_argument_is_one_retry_then_the_row(
    name: str, omitted: dict[str, Any], corrected: dict[str, Any], row: dict[str, Any]
) -> None:
    # drf-mcp answers the omission with an ``isError`` ``validation_error``
    # result keyed by the missing name, which the bridge raises as ``ModelRetry``.
    # Without it the selector raised ``TypeError`` out of ``acall_tool``, and so
    # out of ``call_tool``, ending the run over an argument the model could have
    # supplied. Driven through a real run, so the retry is what the model reads.
    calls = iter([omitted, corrected])

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        if any(part.part_kind == "tool-return" for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args=next(calls))])

    toolset = DRFMCPToolset(lookup_server, _request())
    result = await Agent(FunctionModel(model_fn), toolsets=[toolset]).run("read the row")

    parts = [part for message in result.all_messages() for part in message.parts]
    retries = [part.content for part in parts if part.part_kind == "retry-prompt"]
    # The message names the one argument the detail is keyed by, so the detail
    # is left off: the model reads the sentence once, as on the spec-tools route.
    assert retries == ["Missing required argument(s): `pk`."]
    assert [part.content for part in parts if isinstance(part, ToolReturnPart)] == [row]


async def _retries_for_an_omission(
    name: str,
    omitted: dict[str, Any],
    corrected: dict[str, Any],
    *,
    toolsets: list[Any] | None = None,
    capabilities: list[Any] | None = None,
) -> list[str]:
    """Run one omission then its correction, and return what the model was told."""
    calls = iter([omitted, corrected])

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        if any(part.part_kind == "tool-return" for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args=next(calls))])

    agent = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        toolsets=toolsets,
        capabilities=capabilities,
    )
    result = await agent.run("read the row", deps=AgentDeps(user=AnonymousUser()))
    return [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if part.part_kind == "retry-prompt"
    ]


@pytest.mark.parametrize(("name", "omitted", "corrected", "row"), _LOOKUP_CALLS)
async def test_both_routes_hand_back_an_omission_in_the_same_words(
    name: str, omitted: dict[str, Any], corrected: dict[str, Any], row: dict[str, Any]
) -> None:
    """One spec, one omission, the same retry text by either route.

    The reason the bridge drops a detail its message already names: drf-mcp's
    missing-argument line is the spec-tools toolset's sentence, and the detail
    after it was the only difference a model could read between the routes.
    """
    bridged = await _retries_for_an_omission(
        name, omitted, corrected, toolsets=[DRFMCPToolset(lookup_server, _request())]
    )
    in_process = await _retries_for_an_omission(
        name, omitted, corrected, capabilities=[build_spec_capability(LOOKUP_SPECS)]
    )
    assert len(bridged) == 1
    assert bridged == in_process


@pytest.mark.parametrize("name", ["get_row", "rename_row"])
async def test_both_bridges_require_the_same_arguments(name: str) -> None:
    """One spec, listed in process and over the drf-mcp bridge.

    The reason the ``[drf-mcp]`` and ``[spec-tools]`` floors move together: a
    model is asked for the same arguments whichever way a spec is exposed.
    Asserted against both packages' real schemas, so a pair of floors where only
    one side requires ``pk`` fails here.
    """
    ctx = RunContext(deps=AgentDeps(user=AnonymousUser()), model=TestModel(), usage=RunUsage())
    in_process = await build_spec_capability(LOOKUP_SPECS).get_toolset().get_tools(ctx)
    bridged = await DRFMCPToolset(lookup_server, _request()).get_tools(None)
    required = bridged[name].tool_def.parameters_json_schema.get("required", [])
    assert sorted(required) == sorted(
        in_process[name].tool_def.parameters_json_schema.get("required", [])
    )
    assert "pk" in required


def _recent_rows(*, page: int = 1) -> list[dict[str, Any]]:
    """List rows, taking a parameter the list pipeline strips from every call."""
    return []


async def test_both_routes_refuse_a_list_selector_taking_page() -> None:
    """One spec neither route can serve, refused by both before a call is made.

    A list tool's ``page`` is its pagination argument, which both routes take
    out of the call before the selector runs, so a selector declaring one was
    advertised and ran on its default whatever page the model asked for. The
    floors on both extras are where each route refuses it instead, and they
    move together so one spec is served by both or refused by both. Each
    message is matched on the reason, not only the name, so a refusal for some
    other cause does not pass here.
    """
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_recent_rows,
        output_serializer=Row,
        permission_classes=[AllowAny],
    )
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\], but `page` and `limit`"):
        MCPServer(name="refused").register_selector_tool(
            name="recent_rows", description="List rows.", spec=spec
        )
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\], but `page` and `limit`"):
        build_spec_capability({"recent_rows": spec})
