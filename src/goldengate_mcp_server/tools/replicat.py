"""Replicat process MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

REPLICAT_TOOLS: List[Tool] = [
    Tool(
        name="list_replicats",
        description="List all Replicat processes in a deployment with name and status.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
            },
            "required": ["deployment"],
        },
    ),
    Tool(
        name="get_replicat_status",
        description="Full Replicat status payload for one process as JSON from the GG API.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "replicat_name": {"type": "string", "description": "Name of the Replicat process"},
            },
            "required": ["deployment", "replicat_name"],
        },
    ),
    Tool(
        name="get_replicat_lag",
        description=(
            "Same structured lag shape as get_extract_lag (lag_seconds, baselines, "
            "deviation_sigma, severity, raw_lag). Use for Replicat-specific lag questions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "replicat_name": {"type": "string", "description": "Name of the Replicat process"},
            },
            "required": ["deployment", "replicat_name"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_replicat_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def list_replicats(arguments: Any):
        return await app._list_replicats(arguments["deployment"])

    async def get_replicat_status(arguments: Any):
        return await app._get_replicat_status(
            arguments["deployment"], arguments["replicat_name"]
        )

    async def get_replicat_lag(arguments: Any):
        return await app._get_replicat_lag(arguments["deployment"], arguments["replicat_name"])

    dispatch["list_replicats"] = list_replicats
    dispatch["get_replicat_status"] = get_replicat_status
    dispatch["get_replicat_lag"] = get_replicat_lag
