"""Health, statistics, and error-check MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

HEALTH_TOOLS: List[Tool] = [
    Tool(
        name="get_process_statistics",
        description=(
            "Throughput-oriented stats for one process (inserts, updates, deletes, discards, "
            "operations_per_sec) as returned by the GG API—use with diagnose_lag_issue for context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_type": {
                    "type": "string",
                    "enum": ["extract", "replicat"],
                    "description": "Type of process",
                },
                "process_name": {"type": "string", "description": "Name of the process"},
            },
            "required": ["deployment", "process_type", "process_name"],
        },
    ),
    Tool(
        name="get_deployment_health",
        description=(
            "All extracts and replicats with status and embedded lag payloads where available; "
            "issues[] lists anomalies. Pair with get_*_lag for baselines and severity per process."
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
        name="check_process_errors",
        description=(
            "Recent errors/warnings for one process from the REST API (has_errors, messages). "
            "Use after seeing non-running status or high severity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_type": {
                    "type": "string",
                    "enum": ["extract", "replicat"],
                    "description": "Type of process",
                },
                "process_name": {"type": "string", "description": "Name of the process"},
            },
            "required": ["deployment", "process_type", "process_name"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_health_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def get_process_statistics(arguments: Any):
        return await app._get_process_statistics(
            arguments["deployment"],
            arguments["process_type"],
            arguments["process_name"],
        )

    async def get_deployment_health(arguments: Any):
        return await app._get_deployment_health(arguments["deployment"])

    async def check_process_errors(arguments: Any):
        return await app._check_process_errors(
            arguments["deployment"],
            arguments["process_type"],
            arguments["process_name"],
        )

    dispatch["get_process_statistics"] = get_process_statistics
    dispatch["get_deployment_health"] = get_deployment_health
    dispatch["check_process_errors"] = check_process_errors
