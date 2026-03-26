"""Configuration backup and compare MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

CONFIG_TOOLS: List[Tool] = [
    Tool(
        name="backup_deployment_config",
        description=(
            "JSON-serializable backup of all process configs in the MCP response only"
            "—never writes secrets to the backups/ folder."
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
        name="get_process_config",
        description="One process’ config plus parameter file payload from the GG API.",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_name": {"type": "string", "description": "Name of the process"},
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
        name="compare_deployment_configs",
        description=(
            "Diff current backup_all_configs snapshot vs optional on-disk backup_file JSON. "
            "Without backup_file returns current snapshot and a hint to supply a file path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "backup_file": {
                    "type": "string",
                    "description": "Path to backup JSON to compare against (optional)",
                },
            },
            "required": ["deployment"],
        },
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_config_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def backup_deployment_config(arguments: Any):
        return await app._backup_deployment_config(arguments["deployment"])

    async def get_process_config(arguments: Any):
        return await app._get_process_config(
            arguments["deployment"], arguments["process_name"], arguments["process_type"]
        )

    async def compare_deployment_configs(arguments: Any):
        return await app._compare_deployment_configs(
            arguments["deployment"], arguments.get("backup_file")
        )

    dispatch["backup_deployment_config"] = backup_deployment_config
    dispatch["get_process_config"] = get_process_config
    dispatch["compare_deployment_configs"] = compare_deployment_configs
