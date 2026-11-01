"""Oracle GoldenGate MCP server (REST API monitoring and control)."""

__version__ = "1.2.0"
__author__ = "Vibhor Singh"
__license__ = "MIT"

from .audit import AuditLogger
from .config import Config, DeploymentConfig
from .goldengate_client import GoldenGateAPIError, GoldenGateClient
from .server import GoldenGateMCPServer, main

__all__ = [
    "GoldenGateMCPServer",
    "GoldenGateClient",
    "GoldenGateAPIError",
    "Config",
    "DeploymentConfig",
    "AuditLogger",
    "main",
]
