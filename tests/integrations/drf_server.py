"""A fixture drf-mcp ``MCPServer`` with service tools, for bridge tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework.permissions import AllowAny
from rest_framework_mcp import ChainStep
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_services.exceptions.additional_input_required import AdditionalInputRequired
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.service_spec import ServiceSpec


@dataclass
class AddInput:
    a: int
    b: int


def add_numbers(*, data: AddInput) -> dict[str, Any]:
    return {"result": data.a + data.b}


def reject_input(*, data: AddInput) -> dict[str, Any]:
    raise ServiceValidationError({"a": ["must be even"]})


def deny_by_policy(*, data: AddInput) -> dict[str, Any]:
    raise ServiceError("denied by policy")


def close_the_books() -> dict[str, Any]:
    return {"status": "closed"}


def ask_for_a_reason() -> dict[str, Any]:
    raise AdditionalInputRequired(
        "Say why the books are being closed.", schema={"reason": {"type": "string"}}
    )


# Whether the books are open, which the affordance below reads. Closed unless a
# test opens them, so every call is refused before the service runs. Both
# bridges leave a tool out of a step's tools while its operation condition is
# unmet, so an agent meets this refusal only when the books close between a
# step offering the tool and its call, which is what a test opening them models.
BOOKS = {"open": False}

# Shared with the spec-tools route's tests, which assert the same refusal reads
# identically through both bridges.
REFUSED_SPEC: ServiceSpec[Any, Any, Any] = ServiceSpec(
    permission_classes=[AllowAny],
    service=close_the_books,
    atomic=False,
    affordances=[
        Affordance(code="books_closed", reason="The books are closed.", when=lambda: BOOKS["open"])
    ],
)


# drf-mcp 0.25 refuses to register a tool with no permissions: DRF
# viewset-level and REST_FRAMEWORK defaults do not reach MCP, so an
# omission is an open tool rather than an inherited policy. These are
# bridge fixtures, so AllowAny states "deliberately open" explicitly.
server = MCPServer(name="test")
server.register_service_tool(
    name="add",
    spec=ServiceSpec(permission_classes=[AllowAny], service=add_numbers, input_serializer=AddInput),
)
server.register_service_tool(
    name="invalid",
    spec=ServiceSpec(
        permission_classes=[AllowAny], service=reject_input, input_serializer=AddInput, atomic=False
    ),
)
server.register_service_tool(
    name="denied",
    spec=ServiceSpec(
        permission_classes=[AllowAny],
        service=deny_by_policy,
        input_serializer=AddInput,
        atomic=False,
    ),
)
server.register_service_tool(name="refused", spec=REFUSED_SPEC)
server.register_chain_tool(
    name="refused_chain", steps=[ChainStep("void", REFUSED_SPEC)], atomic=False
)
server.register_service_tool(
    name="needs_input",
    spec=ServiceSpec(permission_classes=[AllowAny], service=ask_for_a_reason, atomic=False),
)
