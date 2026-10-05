"""A drf-mcp ``MCPServer`` serving the row-by-``pk`` specs, for bridge tests.

Registers the same declarations the spec-tools route's tests build a capability
over, so what each route asks of a model is compared for one spec.
"""

from __future__ import annotations

from rest_framework_mcp.server.mcp_server import MCPServer

from tests.integrations.drf_specs_lookup import GET_ROW_SPEC, RENAME_ROW_SPEC

lookup_server = MCPServer(name="lookup")
lookup_server.register_selector_tool(
    name="get_row", description="Read one row by its primary key.", spec=GET_ROW_SPEC
)
lookup_server.register_service_tool(
    name="rename_row", description="Rename one row.", spec=RENAME_ROW_SPEC
)
