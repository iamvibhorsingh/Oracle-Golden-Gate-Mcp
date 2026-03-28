"""Aggregate MCP tool schemas and async dispatch table."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Set

from mcp.types import Tool

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


def all_tools(*, enable_metrics: bool = True) -> List[Tool]:
    """Every Tool definition exposed by list_tools (order: deployment → config)."""
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
    return tools


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
