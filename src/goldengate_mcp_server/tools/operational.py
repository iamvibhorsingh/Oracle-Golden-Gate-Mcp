"""Trails and aggregate health MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

OPERATIONAL_TOOLS: List[Tool] = [
    Tool(
        name="list_trails",
        description="List trail files for the deployment from the GG REST API.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
            },
            "required": ["deployment"],
        },
    ),
    Tool(
        name="get_trail_info",
        description="Detail for one trail (size, sequence, etc.) from the GG API.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "trail_name": {"type": "string", "description": "Name of the trail"},
            },
            "required": ["deployment", "trail_name"],
        },
    ),
    Tool(
        name="get_all_process_health",
        description=(
            "Single-call summary of all extracts/replicats with lag sub-documents where available—"
            "lighter than iterating many tools when you need a full inventory."
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


def register_operational_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def list_trails(arguments: Any):
        return await app._list_trails(arguments["deployment"])

    async def get_trail_info(arguments: Any):
        return await app._get_trail_info(arguments["deployment"], arguments["trail_name"])

    async def get_all_process_health(arguments: Any):
        return await app._get_all_process_health(arguments["deployment"])

    dispatch["list_trails"] = list_trails
    dispatch["get_trail_info"] = get_trail_info
    dispatch["get_all_process_health"] = get_all_process_health
