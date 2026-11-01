"""Configuration backup and compare MCP tools."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

CONFIG_TOOLS: List[Tool] = [
    Tool(
        name="backup_deployment_config",
        description=(
            "JSON-serializable backup of all process configs in the response only. "
            "Use save_config_snapshot to keep one for later drift checks."
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
            "Drift check: diff the live config against a saved snapshot, with a line diff per "
            "modified process. backup_file is a name from list_config_backups. Without it, "
            "returns the current config."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "backup_file": {
                    "type": "string",
                    "description": "Snapshot inside the backup directory (optional)",
                },
            },
            "required": ["deployment"],
        },
    ),
    Tool(
        name="save_config_snapshot",
        description=(
            "Save a redacted config snapshot to the server's backup directory for later "
            "compare_deployment_configs calls. Changes nothing in GoldenGate."
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
        name="list_config_backups",
        description="Saved config snapshots for a deployment, oldest first.",
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

    async def save_config_snapshot(arguments: Any):
        return await app._save_config_snapshot(arguments["deployment"])

    async def list_config_backups(arguments: Any):
        return await app._list_config_backups(arguments["deployment"])

    dispatch["save_config_snapshot"] = save_config_snapshot
    dispatch["list_config_backups"] = list_config_backups
    dispatch["backup_deployment_config"] = backup_deployment_config
    dispatch["get_process_config"] = get_process_config
    dispatch["compare_deployment_configs"] = compare_deployment_configs
