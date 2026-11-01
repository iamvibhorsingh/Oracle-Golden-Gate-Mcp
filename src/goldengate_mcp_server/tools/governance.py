"""Audit trail and security posture MCP tools (admin role)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from mcp.types import Tool

GOVERNANCE_TOOLS: List[Tool] = [
    Tool(
        name="search_audit_log",
        description=(
            "Search the audit trail, rotated files included: who ran what, on which deployment, "
            "with what result. Newest matches, oldest first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Tool name"},
                "user": {"type": "string", "description": "Caller name"},
                "deployment": {"type": "string", "description": "Name of the deployment"},
                "event_type": {
                    "type": "string",
                    "enum": ["action", "result", "security", "authentication"],
                    "description": "Entry type ('security' lists policy denials)",
                },
                "success": {"type": "boolean", "description": "Result entries with this outcome"},
                "since_hours": {"type": "number", "description": "Only the last N hours"},
                "limit": {
                    "type": "integer",
                    "description": "Most entries to return (default 100, max 1000)",
                },
            },
        },
    ),
    Tool(
        name="verify_audit_log",
        description=(
            "Check the audit log's hash chain. Returns ok, the entry count, head_hash and the "
            "first fault if any."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_server_posture",
        description=(
            "Effective security settings: read-only state, write and change-ticket policy, TLS "
            "per deployment, and findings[] for anything weaker than a production baseline."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]

ToolHandler = Callable[[Any], Awaitable[Any]]


def register_governance_handlers(dispatch: Dict[str, ToolHandler], app: Any) -> None:
    async def search_audit_log(arguments: Any):
        filters = (
            "action", "user", "deployment", "event_type", "success", "since_hours", "limit"
        )
        return await app._search_audit_log(
            **{key: arguments[key] for key in filters if key in arguments}
        )

    async def verify_audit_log(arguments: Any):
        return await app._verify_audit_log()

    async def get_server_posture(arguments: Any):
        return app._get_server_posture()

    dispatch["search_audit_log"] = search_audit_log
    dispatch["verify_audit_log"] = verify_audit_log
    dispatch["get_server_posture"] = get_server_posture
