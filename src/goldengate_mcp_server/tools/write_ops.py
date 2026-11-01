"""Start/stop and batch process MCP tools (honors read_only)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

WRITE_TOOLS: List[Tool] = [
    Tool(
        name="start_extract",
        description=(
            "Start one Extract (requires write; blocked when GG_READ_ONLY=true). "
            "Returns GoldenGate API JSON or an error object."
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
        name="stop_extract",
        description="Stop one Extract (requires write).",
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
        name="start_replicat",
        description="Start one Replicat (requires write).",
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
        name="stop_replicat",
        description="Stop one Replicat (requires write).",
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
        name="restart_process",
        description=(
            "Stop one Extract or Replicat, wait until it has stopped, start it and wait until "
            "it runs (requires write). A process that will not stop is not started again."
        ),
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
                "timeout_seconds": {
                    "type": "number",
                    "description": "Seconds to wait for each transition (default 120)",
                    "maximum": 600,
                },
            },
            "required": ["deployment", "process_name", "process_type"],
        },
    ),
    Tool(
        name="batch_start_processes",
        description="Start multiple Extract or Replicat processes (requires write).",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of process names to start",
                },
                "process_type": {
                    "type": "string",
                    "enum": ["extract", "replicat"],
                    "description": "Type of processes",
                },
            },
            "required": ["deployment", "process_names", "process_type"],
        },
    ),
    Tool(
        name="batch_stop_processes",
        description="Stop multiple Extract or Replicat processes (requires write).",
        inputSchema={
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "process_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of process names to stop",
                },
                "process_type": {
                    "type": "string",
                    "enum": ["extract", "replicat"],
                    "description": "Type of processes",
                },
            },
            "required": ["deployment", "process_names", "process_type"],
        },
    ),
]

_GOVERNANCE_PROPERTIES = {
    "dry_run": {
        "type": "boolean",
        "description": "Report current status and what would change, without changing anything",
    },
    "change_ticket": {
        "type": "string",
        "description": "Approved change reference (required on protected deployments)",
    },
}

for _tool in WRITE_TOOLS:
    _tool.inputSchema["properties"].update(_GOVERNANCE_PROPERTIES)

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_write_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def start_extract(arguments: Any):
        return await app._handle_write_operation("start_extract", arguments)

    async def stop_extract(arguments: Any):
        return await app._handle_write_operation("stop_extract", arguments)

    async def start_replicat(arguments: Any):
        return await app._handle_write_operation("start_replicat", arguments)

    async def stop_replicat(arguments: Any):
        return await app._handle_write_operation("stop_replicat", arguments)

    async def restart_process(arguments: Any):
        return await app._handle_write_operation("restart_process", arguments)

    async def batch_start_processes(arguments: Any):
        return await app._batch_start_processes(
            arguments["deployment"], arguments["process_names"], arguments["process_type"]
        )

    async def batch_stop_processes(arguments: Any):
        return await app._batch_stop_processes(
            arguments["deployment"], arguments["process_names"], arguments["process_type"]
        )

    dispatch["start_extract"] = start_extract
    dispatch["stop_extract"] = stop_extract
    dispatch["start_replicat"] = start_replicat
    dispatch["stop_replicat"] = stop_replicat
    dispatch["restart_process"] = restart_process
    dispatch["batch_start_processes"] = batch_start_processes
    dispatch["batch_stop_processes"] = batch_stop_processes
