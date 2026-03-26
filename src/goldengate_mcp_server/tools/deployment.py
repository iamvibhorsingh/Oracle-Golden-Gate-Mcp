"""Deployment-level MCP tools: listings and services."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

DEPLOYMENT_TOOLS: List[Tool] = [
    Tool(
        name="list_deployments",
        description=(
            "List configured GoldenGate deployments with connectivity status (API probe), "
            "reported version when reachable, and base_url. Use first to resolve deployment "
            "names before other tools."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_deployment_info",
        description=(
            "Returns JSON from GET /services/v2/deployments for the named deployment—identity, "
            "services URL, and related deployment metadata from the GoldenGate REST API."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
            },
            "required": ["deployment"],
        },
    ),
    Tool(
        name="list_services",
        description=(
            "List microservices in the deployment (Administration, Distribution, Receiver, etc.) "
            "as returned by the GG REST API."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
            },
            "required": ["deployment"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_deployment_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def list_deployments(arguments: Any):
        return await app._list_deployments()

    async def get_deployment_info(arguments: Any):
        return await app._get_deployment_info(arguments["deployment"])

    async def list_services(arguments: Any):
        return await app._list_services(arguments["deployment"])

    dispatch["list_deployments"] = list_deployments
    dispatch["get_deployment_info"] = get_deployment_info
    dispatch["list_services"] = list_services
