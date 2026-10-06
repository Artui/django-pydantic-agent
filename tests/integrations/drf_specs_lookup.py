"""Specs that read one row by a ``pk`` their selector takes with no default.

Shared by both bridges' tests, so the two routes are asserted against the same
declaration rather than two copies that could drift apart. Neither spec says
anywhere but in the selector's signature that ``pk`` is required: a selector
tool reading a row, and a service whose target lookup is that selector. That is
the case where both routes once advertised ``pk`` as optional and then raised
``TypeError`` out of the run for a call that left it out.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Read, never written: ``rename_row`` returns a renamed copy, so no test leaks a
# rename into the next.
_ROWS: dict[int, dict[str, Any]] = {1: {"id": 1, "name": "first"}}


class Row(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class RenameInput(serializers.Serializer):
    name = serializers.CharField()


def row_by_pk(*, pk: int) -> dict[str, Any]:
    """Read one row by its primary key."""
    return _ROWS[pk]


def rename_row(*, instance: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Rename one row."""
    return {**instance, "name": data["name"]}


GET_ROW_SPEC: SelectorSpec[Any, Any] = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=row_by_pk,
    output_serializer=Row,
    permission_classes=[AllowAny],
)

# The row arrives through ``instance_selector_spec``, so ``pk`` is the lookup's
# parameter and not the input serializer's field: the case where a tool changing
# one row never told the model which argument names that row.
RENAME_ROW_SPEC: ServiceSpec[Any, Any, Any] = ServiceSpec(
    service=rename_row,
    input_serializer=RenameInput,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=row_by_pk),
    output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=Row),
    permission_classes=[AllowAny],
    atomic=False,
)

SPECS: dict[str, Any] = {"get_row": GET_ROW_SPEC, "rename_row": RENAME_ROW_SPEC}
