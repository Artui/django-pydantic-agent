"""In-process bridge from a ``drf-mcp-server`` registry to a Pydantic-AI toolset.

Requires the ``django-pydantic-agent[drf-mcp]`` extra, so consumers import this
module lazily and the dependency on ``rest_framework_mcp`` stays optional.
"""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from pydantic_ai import ModelRetry, ToolFailed
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema
from rest_framework_mcp import JsonRpcError, JsonRpcErrorCode

from django_pydantic_agent.constants import DESTRUCTIVE_METADATA_KEY

# A no-op validator: the parameter schemas advertised to the model come verbatim
# from drf-mcp's ``tools/list`` (advisory, not a Pydantic model), and the real
# validation is drf-mcp's own serializer at call time — the same split the HTTP
# transport has.
_TOOL_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.any_schema())

# The heading of the per-step list of tools an unmet operation condition left
# out, worded as ``djangorestframework-pydantic-ai`` words it for the spec-tools
# route. A consumer can expose one spec either way, and the model is taught one
# sentence for a missing operation; a test asserts it against that package's
# real output, so a change on either side fails there rather than drifting.
_UNAVAILABLE_INSTRUCTION = (
    "- These operations exist but cannot be performed right now, so they are not among your "
    "tools. If the user asks for one, say it is unavailable at the moment and give the reason "
    "listed for it, rather than guessing why:"
)


class DRFMCPToolset(AbstractToolset[Any]):
    """Exposes a drf-mcp ``MCPServer``'s tools as a Pydantic-AI toolset.

    Built per request, so the agent acts as the request's logged-in user. Both
    schemas and execution route through drf-mcp's public in-process surface
    (``MCPServer.list_tools`` / ``acall_tool``, drf-mcp 0.9+), so the advertised
    parameters, serializer validation and permissions match the HTTP transport
    exactly — without the network hop. Tool definitions carry the default
    ``kind="function"``, the in-process kind the run loop calls itself; an
    ``external`` tool would instead be deferred to the client and never run.

    **Definitions once, availability every step.** drf-mcp leaves a tool out of
    ``tools/list`` while an operation-scope affordance refuses it, and whether one
    does can change mid-run — an admin closes the books. So the definitions are
    listed once, with every tool the user may see, and each step asks drf-mcp
    which of them a fresh listing would leave out (``aunavailable_tools``,
    drf-mcp 0.48+): those are not offered that step, and the instructions name
    each with its ``reason``, so a model asked for one can say why rather than
    guess or deny the operation exists. A server declaring no such condition
    answers that without leaving the event loop.

    Failures split three ways, along MCP's protocol-vs-tool boundary:

    - JSON-RPC ``-32602`` and tool-level ``validation_error`` and
      ``input_required`` results raise ``pydantic_ai.ModelRetry``, so the model
      calls again with the field errors fixed or the missing input supplied,
      instead of the run dying;
    - every other tool-level failure (``service_error``, ``not_found``, a
      timeout, an oversized result) raises ``pydantic_ai.ToolFailed``, so the
      model reads the sentence and the call is recorded ``outcome="failed"``;
    - protocol faults (auth, rate limits, an internal error) raise
      ``RuntimeError`` and abort the run.

    The mapping is the one ``djangorestframework-pydantic-ai`` applies to the same
    exceptions raised in process, and a refusal reads identically through both:
    ``The books are closed. (code: books_closed)``.

    Args:
        server: The drf-mcp ``MCPServer`` whose registry is bridged.
        request: The request carried into every call; its ``user`` is the
            acting user.
        exclude_names: Names the ``@tool`` registry has already claimed. A
            colliding drf-mcp tool is skipped, so the registry wins — the rule
            ``build_tool_catalog`` applies — because pydantic-ai raises
            ``UserError`` for a duplicate name at run time.
        max_retries: Per-tool retry budget: how many times a ``ModelRetry`` is
            fed back to the model before the run aborts. The default matches
            pydantic-ai's own function-tool default.
    """

    def __init__(
        self,
        server: Any,
        request: HttpRequest,
        *,
        exclude_names: frozenset[str] = frozenset(),
        max_retries: int = 1,
    ) -> None:
        self._server = server
        self._request = request
        self._exclude_names = exclude_names
        self._max_retries = max_retries
        # Loaded lazily in ``get_tools``: drf-mcp's ``tools/list`` may touch the
        # DB for per-user listing permissions, which Django forbids on the async
        # event loop this is constructed in. Every tool the user may see, the
        # unavailable ones included, so one that becomes available mid-run has a
        # definition to offer.
        self._tool_defs: list[ToolDefinition] | None = None

    @property
    def id(self) -> str | None:
        return "drf-mcp"

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """The tools this step may call: every definition, less the unavailable.

        Definitions come from drf-mcp's ``tools/list`` once per toolset, which is
        once per request. Availability is asked again here every step, because
        pydantic-ai calls this once per step and a condition may flip between
        two: offering a tool drf-mcp would leave out only invites a refusal, and
        withholding one that became available hides an operation the model could
        now perform. The call enforces every condition whatever was offered.
        """
        if self._tool_defs is None:
            self._tool_defs = await sync_to_async(self._load_tool_defs)()
        unavailable = await self._unavailable_tools()
        return {
            tool_def.name: ToolsetTool(
                toolset=self,
                tool_def=tool_def,
                max_retries=self._max_retries,
                args_validator=_TOOL_ARGS_VALIDATOR,
            )
            for tool_def in self._tool_defs
            if tool_def.name not in unavailable
        }

    async def get_instructions(self, ctx: Any) -> str | None:
        """Name each tool left out this step, with the reason its condition gives.

        ``get_tools`` withholds such a tool, so without this the model sees
        neither it nor any sign it exists, and a user asking for it gets a guess
        where a sentence was available. ``None`` when nothing is left out, which
        is every step for a server declaring no operation condition.

        Asked separately from ``get_tools``, as the spec-tools route asks: the
        run context carries no key that could safely scope one answer to one
        step, and asking twice costs a second evaluation of conditions that read
        only seeds. A condition flipping between the two reads can leave one
        step's tools and instructions disagreeing about a tool; the call's own
        enforcement is authoritative either way.
        """
        unavailable = await self._unavailable_tools()
        if not unavailable:
            return None
        return _unavailable_instruction(unavailable)

    async def _unavailable_tools(self) -> dict[str, Any]:
        """Each tool drf-mcp would leave out of a listing now, with the condition.

        In drf-mcp's registry order, so a step always reads the same way. A name
        ``exclude_names`` claimed is dropped: the registry's tool of that name is
        the one offered, and telling the model it cannot run would be about a
        tool it never sees.
        """
        unavailable: dict[str, Any] = await self._server.aunavailable_tools(
            user=self._request.user, request=self._request
        )
        return {
            name: affordance
            for name, affordance in unavailable.items()
            if name not in self._exclude_names
        }

    def _load_tool_defs(self) -> list[ToolDefinition]:
        """Page through drf-mcp's ``tools/list``, mapping each tool to a def.

        The merged ``inputSchema`` is used verbatim, so nothing the model could
        send over HTTP is silently dropped in process. ``include_unavailable``
        lists the tools an unmet condition would leave out too, since this list
        outlives the step it is taken in; ``get_tools`` filters them per step.
        """
        defs: list[ToolDefinition] = []
        cursor: str | None = None
        while True:
            payload = self._server.list_tools(
                cursor,
                user=self._request.user,
                request=self._request,
                include_unavailable=True,
            )
            if isinstance(payload, JsonRpcError):
                raise RuntimeError(f"drf-mcp tools/list failed: {payload.message}")
            for tool in payload["tools"]:
                if tool["name"] in self._exclude_names:
                    continue
                annotations = tool.get("annotations") or {}
                # Destructiveness rides ``metadata`` so ``ToolGuard`` can gate a
                # bridged mutation. Key on ``readOnlyHint``, not
                # ``destructiveHint``: drf-mcp omits the latter on read-only
                # tools, so only the former lets a project's per-registration
                # ``annotations`` override exempt a mutation.
                metadata = (
                    {DESTRUCTIVE_METADATA_KEY: True}
                    if annotations.get("readOnlyHint") is False
                    else None
                )
                # Passing drf-mcp's ``outputSchema`` through as ``return_schema``
                # is what lets a harness ``CodeMode`` capability render the tool
                # as a typed stub rather than ``-> Any``. Absent when a project
                # turns ``INCLUDE_OUTPUT_SCHEMA`` off.
                output_schema = tool.get("outputSchema")
                defs.append(
                    ToolDefinition(
                        name=tool["name"],
                        description=tool.get("description"),
                        parameters_json_schema=tool["inputSchema"],
                        metadata=metadata,
                        return_schema=output_schema,
                        include_return_schema=output_schema is not None,
                    )
                )
            cursor = payload.get("nextCursor")
            if cursor is None:
                break
        return defs

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: Any,
    ) -> Any:
        result = await self._server.acall_tool(
            name, tool_args, user=self._request.user, request=self._request
        )
        if isinstance(result, JsonRpcError):
            if result.code == JsonRpcErrorCode.INVALID_PARAMS:
                # Since drf-mcp 0.24.0 `-32602` covers an unknown tool as well
                # as malformed arguments (it emitted `-32004` for the former
                # before), so the two can no longer be told apart by code. Both
                # are retried deliberately: `-32602` is by definition a fault in
                # the request the model produced, and both a wrong name and
                # wrong arguments are things it can change. pydantic-ai bounds
                # the retries, so an unfixable call still ends the run.
                raise ModelRetry(
                    _retry_message(
                        result.message,
                        (result.data or {}).get("detail"),
                        available=self._advertised_names(name),
                    )
                )
            # Everything else — auth, rate limits, an internal fault — is not
            # something the model can rewrite its way out of.
            raise RuntimeError(f"drf-mcp tool {name!r} failed: {result.message}")
        if result.get("isError"):
            error = _parse_tool_error(result)
            error_type: Any = error.get("type")
            if error_type == "validation_error":
                # Kept on separate lines: Python 3.11's tracer attributes a
                # multi-line ``raise X(...)`` to the argument line, leaving the
                # ``raise`` line uncovered and tripping the 100% gate.
                message = error.get("message", "invalid arguments")
                raise ModelRetry(_retry_message(message, error.get("detail")))
            if error_type == "input_required":
                # drf-mcp degrades a service's request for more input to this
                # result when the caller cannot be asked mid-call, which an
                # in-process caller never can. "Call me again with these" is the
                # retry channel's own meaning, and it is what the spec-tools
                # route raises for the same exception.
                prompt = _missing_input_prompt(error)
                raise ModelRetry(prompt)
            # **Raised, not returned.** This returned ``{"error": error}`` as the
            # tool's value, which pydantic-ai marks ``outcome="success"``: every
            # refusal, missing row and timeout reached a transport streaming the
            # run as a completed call, told apart from a real result only by the
            # payload's wording. ``ToolFailed`` hands the model the same sentence,
            # spends no retry budget and prepends no correction instructions, and
            # marks the return ``outcome="failed"``.
            failure = _failure_message(error)
            raise ToolFailed(failure)
        return result.get("structuredContent", result.get("content"))

    def _advertised_names(self, name: str) -> list[str] | None:
        """The tools this toolset offers, when the failure looks like a bad name.

        Naming them is what makes retrying an invented name worth doing rather
        than merely survivable. ``None`` when there is nothing useful to add:
        the name *was* advertised, so the fault is the arguments, or
        ``get_tools`` has not run and the cache is empty.
        """
        if self._tool_defs is None:
            return None
        names: list[str] = [d.name for d in self._tool_defs]
        return None if name in names else names


def _unavailable_instruction(unavailable: dict[str, Any]) -> str:
    """The heading, then one line per tool left out: its name and its ``reason``.

    The ``code`` is left out, as the spec-tools route leaves it out: it is for
    programs and for tying a refusal to a row's ``affordances``, and a model
    relaying this to a person has no use for it.
    """
    lines = [_UNAVAILABLE_INSTRUCTION]
    lines.extend(f"  - `{name}`: {affordance.reason}" for name, affordance in unavailable.items())
    return "\n".join(lines)


def _parse_tool_error(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the ``{"error": {...}}`` payload from an ``isError`` result.

    drf-mcp encodes it as JSON text in ``content[0]``. Falls back to a generic
    shape rather than raising, so a changed encoding cannot turn reporting an
    error into a second error.
    """
    content = result.get("content") or []
    text: Any = content[0].get("text", "") if content else ""
    try:
        error = json.loads(text)["error"]
    except (ValueError, KeyError, TypeError):
        return {"type": "unknown", "message": str(text) or "tool error"}
    return error if isinstance(error, dict) else {"type": "unknown", "message": str(error)}


# The error keys that name a failure, in suffix order, with the label each is
# written under. ``code`` is a refusal's stable name (drf-mcp 0.45+ sends it for
# an ``ActionUnavailable``); ``failedStep`` is the chain step that failed.
_FAILURE_LABELS: tuple[tuple[str, str], ...] = (("code", "code"), ("failedStep", "step"))


def _failure_message(error: dict[str, Any]) -> str:
    """Compose the ``ToolFailed`` text: the server's sentence, then what names it.

    While the error came back as a returned dict, ``code`` and ``failedStep`` were
    keys the model could read. ``ToolFailed`` carries a string and nothing else,
    so they ride as a suffix rather than being dropped. The ``(code: ...)`` form
    is the one ``djangorestframework-pydantic-ai`` writes for the same refusal
    raised in process, so a model reads one wording whichever route a tool
    arrived by, and can match it to the ``code`` a row's ``affordances`` answer
    advertised. It is written for the model and for a person reading the tool
    call; a program wanting the code reads it where drf-mcp serves it, not out of
    this sentence.
    """
    message: str = str(error.get("message") or "tool error")
    labels: list[str] = [
        f"{label}: {error[key]}" for key, label in _FAILURE_LABELS if error.get(key)
    ]
    if not labels:
        return message
    return f"{message} ({', '.join(labels)})"


def _missing_input_prompt(error: dict[str, Any]) -> str:
    """Compose the ``ModelRetry`` text for ``input_required``: the service's
    message, plus the names it wants the answer back under.

    Worded as ``djangorestframework-pydantic-ai`` words the same request, for its
    reason: ``requestedInput`` is a JSON-Schema *properties* mapping keyed by
    input name, the model is about to call the same tool again, and that tool's
    parameter schema already describes each argument. The names are what to add;
    a second, differently shaped description in prose is how a model ends up
    inventing a nested object.
    """
    message: str = str(error.get("message") or "additional input required")
    requested: Any = error.get("requestedInput")
    # Absent when the service named no schema; drf-mcp sends a mapping otherwise.
    if not requested:
        return message
    names: str = ", ".join(f"`{name}`" for name in requested)
    return f"{message} Call this tool again, additionally supplying: {names}."


def _retry_message(message: str, detail: Any, *, available: list[str] | None = None) -> str:
    """Compose the ``ModelRetry`` text: the server's message, plus whichever of
    field-level detail or the available tool names helps.

    Never both — a `-32602` is about either the name or the arguments, and the
    caller already decided which by whether the name was advertised.
    """
    if available is not None:
        names: str = ", ".join(sorted(available)) or "none"
        return f"{message}. Available tools: {names}."
    if not detail:
        return message
    return f"{message}: {json.dumps(detail, default=str)}"


__all__ = ["DRFMCPToolset"]
