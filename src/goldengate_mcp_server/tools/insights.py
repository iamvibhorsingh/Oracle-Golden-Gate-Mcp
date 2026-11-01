"""Fleet health and process runtime detail MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

_PROCESS_PROPERTIES = {
    "deployment": {"type": "string", "description": "Name of the deployment"},
    "process_type": {
        "type": "string",
        "enum": ["extract", "replicat"],
        "description": "Type of process",
    },
    "process_name": {"type": "string", "description": "Name of the process"},
}

INSIGHTS_TOOLS: List[Tool] = [
    Tool(
        name="get_fleet_health",
        description=(
            "Health of every deployment at once: summary counts plus attention[], the abended, "
            "stopped and lagging processes ranked worst first. Start here for 'is anything wrong?'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Limit to these deployments (default: all you can access)",
                },
                "lag_threshold_seconds": {
                    "type": "number",
                    "description": "Lag at or above this is flagged (default 300)",
                },
            },
        },
    ),
    Tool(
        name="get_process_report",
        description=(
            "Tail of a process report file plus ogg_messages[], its parsed OGG-nnnnn errors and "
            "warnings. The best source for why a process abended."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                **_PROCESS_PROPERTIES,
                "tail_lines": {
                    "type": "integer",
                    "description": "Lines from the end of the report (default 200, max 1000)",
                },
                "report": {
                    "type": "string",
                    "description": "Report file, e.g. 'EXT10.rpt' (default: the current report)",
                },
            },
            "required": ["deployment", "process_type", "process_name"],
        },
    ),
    Tool(
        name="get_process_checkpoints",
        description="Read and write checkpoint positions of a process from the GG API.",
        inputSchema={
            "type": "object",
            "properties": {
                **_PROCESS_PROPERTIES,
                "history": {
                    "type": "integer",
                    "description": "Historical checkpoint records to include (default 0)",
                },
            },
            "required": ["deployment", "process_type", "process_name"],
        },
    ),
    Tool(
        name="get_deployment_messages",
        description="Recent Administration Server messages for a deployment, newest last.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "limit": {
                    "type": "integer",
                    "description": "Most messages to return (default 50, max 500)",
                },
            },
            "required": ["deployment"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_insights_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def get_fleet_health(arguments: Any):
        return await app._get_fleet_health(
            arguments.get("deployments"),
            arguments.get("lag_threshold_seconds", 300),
        )

    async def get_process_report(arguments: Any):
        return await app._get_process_report(
            arguments["deployment"],
            arguments["process_type"],
            arguments["process_name"],
            arguments.get("tail_lines", 200),
            arguments.get("report"),
        )

    async def get_process_checkpoints(arguments: Any):
        return await app._get_process_checkpoints(
            arguments["deployment"],
            arguments["process_type"],
            arguments["process_name"],
            arguments.get("history", 0),
        )

    async def get_deployment_messages(arguments: Any):
        return await app._get_deployment_messages(
            arguments["deployment"], arguments.get("limit", 50)
        )

    dispatch["get_fleet_health"] = get_fleet_health
    dispatch["get_process_report"] = get_process_report
    dispatch["get_process_checkpoints"] = get_process_checkpoints
    dispatch["get_deployment_messages"] = get_deployment_messages
