"""Aggregate MCP tool schemas and async dispatch table."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Set

from mcp.types import Tool, ToolAnnotations

from .config_mgmt import CONFIG_TOOLS, register_config_handlers
from .deployment import DEPLOYMENT_TOOLS, register_deployment_handlers
from .diagnostics_tools import DIAGNOSTICS_TOOLS, register_diagnostics_handlers
from .extract import EXTRACT_TOOLS, register_extract_handlers
from .health import HEALTH_TOOLS, register_health_handlers
from .operational import OPERATIONAL_TOOLS, register_operational_handlers
from .replicat import REPLICAT_TOOLS, register_replicat_handlers
from .write_ops import WRITE_TOOLS, register_write_handlers

ToolHandler = Callable[[Any], Awaitable[Any]]

# Tools that require MetricsStore / DiagnosticsEngine (background collection).
METRICS_DEPENDENT_TOOLS: Set[str] = {
    "diagnose_lag_issue",
    "get_performance_baseline",
    "get_lag_trend",
}


# Tools that change GoldenGate state; hidden from list_tools in read-only mode.
WRITE_TOOL_NAMES: Set[str] = {t.name for t in WRITE_TOOLS}

# Write tools that interrupt replication.
DESTRUCTIVE_TOOLS: Set[str] = {"stop_extract", "stop_replicat", "batch_stop_processes"}


def _annotate(tool: Tool) -> Tool:
    if tool.name in WRITE_TOOL_NAMES:
        annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=tool.name in DESTRUCTIVE_TOOLS,
            # Setting a process to running/stopped twice has the same effect as once.
            idempotentHint=True,
            openWorldHint=True,
        )
    else:
        annotations = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
    return tool.model_copy(update={"annotations": annotations})


def all_tools(*, enable_metrics: bool = True, read_only: bool = False) -> List[Tool]:
    """Every Tool definition exposed by list_tools (order: deployment → config).

    Metrics tools are omitted when metrics are off; write tools are omitted in read-only
    mode (their handlers still reject calls, so hiding them is not the only guard).
    """
    tools = [
        *DEPLOYMENT_TOOLS,
        *EXTRACT_TOOLS,
        *REPLICAT_TOOLS,
        *HEALTH_TOOLS,
        *WRITE_TOOLS,
        *DIAGNOSTICS_TOOLS,
        *OPERATIONAL_TOOLS,
        *CONFIG_TOOLS,
    ]
    if not enable_metrics:
        tools = [t for t in tools if t.name not in METRICS_DEPENDENT_TOOLS]
    if read_only:
        tools = [t for t in tools if t.name not in WRITE_TOOL_NAMES]
    return [_annotate(t) for t in tools]


def build_dispatch(app: Any, *, enable_metrics: bool = True) -> Dict[str, ToolHandler]:
    """Map tool name → async handler(arguments: dict)."""
    dispatch: Dict[str, ToolHandler] = {}
    register_deployment_handlers(dispatch, app)
    register_extract_handlers(dispatch, app)
    register_replicat_handlers(dispatch, app)
    register_health_handlers(dispatch, app)
    register_write_handlers(dispatch, app)
    register_diagnostics_handlers(dispatch, app)
    register_operational_handlers(dispatch, app)
    register_config_handlers(dispatch, app)
    if not enable_metrics:
        for name in METRICS_DEPENDENT_TOOLS:
            dispatch.pop(name, None)
    return dispatch
