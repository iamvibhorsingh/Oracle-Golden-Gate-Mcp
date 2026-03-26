"""Extract process MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

EXTRACT_TOOLS: List[Tool] = [
    Tool(
        name="list_extracts",
        description="List all Extract processes in a deployment with name and status from the API.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
            },
            "required": ["deployment"],
        },
    ),
    Tool(
        name="get_extract_status",
        description=(
            "Full Extract status payload for one process (state, messages, etc.) "
            "as JSON from the GG API."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "extract_name": {"type": "string", "description": "Name of the Extract process"},
            },
            "required": ["deployment", "extract_name"],
        },
    ),
    Tool(
        name="get_extract_lag",
        description=(
            "Structured lag for an Extract: lag_seconds, 7-day baseline (mean, p95, std dev), "
            "deviation_sigma, severity, raw_lag, collected_at_utc. Baseline fields null if "
            "insufficient history. For deployment-wide view use get_deployment_health."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "extract_name": {"type": "string", "description": "Name of the Extract process"},
            },
            "required": ["deployment", "extract_name"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_extract_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def list_extracts(arguments: Any):
        return await app._list_extracts(arguments["deployment"])

    async def get_extract_status(arguments: Any):
        return await app._get_extract_status(arguments["deployment"], arguments["extract_name"])

    async def get_extract_lag(arguments: Any):
        return await app._get_extract_lag(arguments["deployment"], arguments["extract_name"])

    dispatch["list_extracts"] = list_extracts
    dispatch["get_extract_status"] = get_extract_status
    dispatch["get_extract_lag"] = get_extract_lag
