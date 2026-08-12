"""In-process bridge from a ``drf-mcp-server`` registry to a Pydantic-AI toolset.

Requires the ``django-pydantic-agent[drf-mcp]`` extra, so consumers import this
module lazily and the dependency on ``rest_framework_mcp`` stays optional.
"""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from pydantic_ai import ModelRetry
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


class DRFMCPToolset(AbstractToolset[Any]):
    """Exposes a drf-mcp ``MCPServer``'s tools as a Pydantic-AI toolset.

    Built per request, so the agent acts as the request's logged-in user. Both
    schemas and execution route through drf-mcp's public in-process surface
    (``MCPServer.list_tools`` / ``acall_tool``, drf-mcp 0.9+), so the advertised
    parameters, serializer validation and permissions match the HTTP transport
    exactly — without the network hop. Tool definitions carry the default
    ``kind="function"``, the in-process kind the run loop calls itself; an
    ``external`` tool would instead be deferred to the client and never run.

    Failures split three ways, along MCP's protocol-vs-tool boundary:

    - JSON-RPC ``-32602`` and tool-level ``validation_error`` results raise
      :class:`pydantic_ai.ModelRetry`, so the model retries with the field
      errors instead of the run dying;
    - other tool-level failures (``service_error`` / ``not_found``) are returned
      as the tool's content, for the model to read;
    - protocol faults (auth, rate limits, an internal error) raise
      ``RuntimeError`` and abort the run.

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
        # event loop this is constructed in.
        self._tool_defs: list[ToolDefinition] | None = None

    @property
    def id(self) -> str | None:
        return "drf-mcp"

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """Load tool defs from drf-mcp's ``tools/list`` once, then wrap them."""
        if self._tool_defs is None:
            self._tool_defs = await sync_to_async(self._load_tool_defs)()
        return {
            tool_def.name: ToolsetTool(
                toolset=self,
                tool_def=tool_def,
                max_retries=self._max_retries,
                args_validator=_TOOL_ARGS_VALIDATOR,
            )
            for tool_def in self._tool_defs
        }

    def _load_tool_defs(self) -> list[ToolDefinition]:
        """Page through drf-mcp's ``tools/list``, mapping each tool to a def.

        The merged ``inputSchema`` is used verbatim, so nothing the model could
        send over HTTP is silently dropped in process.
        """
        defs: list[ToolDefinition] = []
        cursor: str | None = None
        while True:
            payload = self._server.list_tools(
                cursor, user=self._request.user, request=self._request
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
            if error.get("type") == "validation_error":
                # Kept on separate lines: Python 3.11's tracer attributes a
                # multi-line ``raise X(...)`` to the argument line, leaving the
                # ``raise`` line uncovered and tripping the 100% gate.
                message = error.get("message", "invalid arguments")
                raise ModelRetry(_retry_message(message, error.get("detail")))
            return {"error": error}
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
