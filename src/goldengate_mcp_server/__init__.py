"""
GoldenGate MCP Server

A Model Context Protocol server for managing and monitoring Oracle GoldenGate
deployments through REST APIs.

Features:
- Comprehensive GoldenGate monitoring and management
- Read-only mode by default for safety
- Secure credential management
- Comprehensive audit logging
- Support for GoldenGate 21.x and 23.x
"""

__version__ = "1.0.0"
__author__ = "Your Organization"
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
