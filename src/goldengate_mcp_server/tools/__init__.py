"""
Per-domain MCP tool definitions and dispatch registration.

``server.GoldenGateMCPServer`` imports :func:`registry.all_tools` and
:func:`registry.build_dispatch` so schemas and routing stay modular.
"""

from .registry import ToolHandler, all_tools, build_dispatch

__all__ = ["ToolHandler", "all_tools", "build_dispatch"]
