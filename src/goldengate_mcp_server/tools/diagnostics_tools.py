"""Diagnostics, baselines, and troubleshooting MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

DIAGNOSTICS_TOOLS: List[Tool] = [
    Tool(
        name="diagnose_lag_issue",
        description=(
            "Numeric-first diagnosis: current_lag_seconds, baseline_*, deviation_sigma, "
            "severity, contributing_factors; text in likely_causes/recommendations is supporting."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_name": {"type": "string", "description": "Extract or Replicat name"},
                "process_type": {
                    "type": "string",
                    "enum": ["extract", "replicat"],
                    "description": "Type of process",
                },
            },
            "required": ["deployment", "process_name", "process_type"],
        },
    ),
    Tool(
        name="get_performance_baseline",
        description=(
            "7-day lag baseline stats (mean, std, p95, data_points) plus hourly_pattern "
            "from MetricsStore."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_name": {"type": "string", "description": "Name of the process"},
            },
            "required": ["deployment", "process_name"],
        },
    ),
    Tool(
        name="get_lag_trend",
        description=(
            "Last 24h lag history from MetricsStore with min/max/mean and simple direction "
            "flag when enough points exist."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_name": {"type": "string", "description": "Name of the process"},
            },
            "required": ["deployment", "process_name"],
        },
    ),
    Tool(
        name="check_database_correlation",
        description=(
            "Joins optional DB monitor metrics with deployment health "
            "when oracledb monitoring is configured via env."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "database_name": {
                    "type": "string",
                    "description": "Database identifier from monitoring config",
                },
            },
            "required": ["deployment", "database_name"],
        },
    ),
    Tool(
        name="get_troubleshooting_guide",
        description=(
            "Structured checklist (steps, common_causes) from static symptom keys "
            "such as high_lag or abended."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symptoms": {
                    "type": "object",
                    "description": "e.g. {\"high_lag\": true, \"abended\": false}",
                }
            },
            "required": ["symptoms"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_diagnostics_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def diagnose_lag_issue(arguments: Any):
        return await app._diagnose_lag_issue(
            arguments["deployment"], arguments["process_name"], arguments["process_type"]
        )

    async def get_performance_baseline(arguments: Any):
        return await app._get_performance_baseline(
            arguments["deployment"], arguments["process_name"]
        )

    async def get_lag_trend(arguments: Any):
        return await app._get_lag_trend(arguments["deployment"], arguments["process_name"])

    async def check_database_correlation(arguments: Any):
        return await app._check_database_correlation(
            arguments["deployment"], arguments["database_name"]
        )

    async def get_troubleshooting_guide(arguments: Any):
        return app._get_troubleshooting_guide(arguments["symptoms"])

    dispatch["diagnose_lag_issue"] = diagnose_lag_issue
    dispatch["get_performance_baseline"] = get_performance_baseline
    dispatch["get_lag_trend"] = get_lag_trend
    dispatch["check_database_correlation"] = check_database_correlation
    dispatch["get_troubleshooting_guide"] = get_troubleshooting_guide
