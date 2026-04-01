#!/usr/bin/env python3
"""
Oracle GoldenGate MCP Server

Provides Model Context Protocol interface for managing and monitoring
Oracle GoldenGate deployments through REST APIs.

Security Features:
- Read-only mode by default
- TLS/SSL verification
- Secure credential management
- Comprehensive audit logging
- Input validation and sanitization
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server import Server
from mcp.types import TextContent, Tool

from .audit import AuditLogger
from .config import Config
from .database_monitor import DatabaseMonitor, create_db_config_from_env
from .diagnostics import DiagnosticsEngine
from .goldengate_client import GoldenGateClient
from .metrics_store import MetricsStore
from .redaction import redact_error_text
from .services import (
    ConfigToolsMixin,
    DeploymentMixin,
    DiagnosticsMixin,
    HealthMixin,
    MetricsCollectionMixin,
    OperationalMixin,
    ProcessReadMixin,
    ServerCoreMixin,
    WriteMixin,
)
from .tools import all_tools, build_dispatch, METRICS_DEPENDENT_TOOLS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class GoldenGateMCPServer(
    ServerCoreMixin,
    DeploymentMixin,
    ProcessReadMixin,
    HealthMixin,
    WriteMixin,
    MetricsCollectionMixin,
    DiagnosticsMixin,
    OperationalMixin,
    ConfigToolsMixin,
):
    def __init__(self, config: Config):
        self.config = config
        self.server = Server("goldengate-mcp-server")
        self.clients: Dict[str, GoldenGateClient] = {}
        self.audit_logger = AuditLogger(config.audit_log_path)

        self._enable_metrics = config.enable_metrics

        if self._enable_metrics:
            self.metrics_store = MetricsStore(db_path=config.metrics_db_path)
            self.metrics_store.cleanup_old_data(retention_days=30)
        else:
            self.metrics_store = None

        self.db_monitor = DatabaseMonitor(create_db_config_from_env())

        self._initialize_clients()

        if self._enable_metrics:
            self.diagnostics = DiagnosticsEngine(self.metrics_store, self.clients)
        else:
            self.diagnostics = None

        self._metrics_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._last_metrics_cleanup_monotonic = time.monotonic()

        self._register_handlers()

        if self._enable_metrics:
            self._start_metrics_collection()

        logger.info("GoldenGate MCP Server initialized")
        logger.info("Read-only mode: %s", config.read_only)
        logger.info("Deployments configured: %s", len(self.clients))
        logger.info("Metrics collection: %s", "enabled" if self._enable_metrics else "disabled")
        logger.info("Diagnostics engine: %s", "enabled" if self._enable_metrics else "disabled")

    def _initialize_clients(self) -> None:
        for deployment in self.config.deployments:
            try:
                client = GoldenGateClient(
                    base_url=deployment.base_url,
                    username=deployment.username,
                    password=deployment.password,
                    verify_ssl=deployment.verify_ssl,
                    ca_bundle=deployment.ca_bundle,
                    timeout=self.config.request_timeout,
                    cache_ttl=self.config.cache_ttl_seconds,
                    max_concurrent=self.config.max_concurrent_requests,
                    requests_per_second=self.config.requests_per_second,
                    deployment_name=deployment.name,
                )
                self.clients[deployment.name] = client
                logger.info("Initialized client for deployment: %s", deployment.name)
            except Exception as e:
                logger.error(
                    "Failed to initialize client for %s: %s",
                    deployment.name,
                    e,
                )

    def _register_handlers(self) -> None:
        self._tool_handlers = build_dispatch(self, enable_metrics=self._enable_metrics)

        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return all_tools(enable_metrics=self._enable_metrics)

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> List[TextContent]:
            self.audit_logger.log_action(
                action=name,
                arguments=arguments,
                timestamp=datetime.utcnow(),
            )
            try:
                handler = self._tool_handlers.get(name)
                if handler:
                    result = await handler(arguments)
                else:
                    result = {"error": f"Unknown tool: {name}"}

                self.audit_logger.log_result(
                    action=name,
                    success=not isinstance(result, dict) or "error" not in result,
                    timestamp=datetime.utcnow(),
                )
                return [TextContent(type="text", text=self._format_result(result))]
            except Exception as e:
                logger.error("Error executing tool %s: %s", name, e, exc_info=True)
                safe_err = redact_error_text(str(e))
                self.audit_logger.log_result(
                    action=name,
                    success=False,
                    error=safe_err,
                    timestamp=datetime.utcnow(),
                )
                dep = (
                    arguments.get("deployment")
                    if isinstance(arguments, dict)
                    else None
                )
                dep_phrase = f"'{dep}'" if dep else "from your request"
                hint = (
                    f"Error executing {name}: {safe_err}. "
                    f"Suggested next step: verify deployment {dep_phrase} is configured "
                    "and reachable via list_deployments, or retry in ~30 s if transient."
                )
                return [TextContent(type="text", text=hint)]

    async def shutdown(self) -> None:
        logger.info("Shutting down GoldenGate MCP Server...")

        self._shutdown_event.set()

        if self._metrics_task and not self._metrics_task.done():
            try:
                await asyncio.wait_for(self._metrics_task, timeout=10.0)
                logger.info("Metrics collection task stopped")
            except asyncio.TimeoutError:
                logger.warning(
                    "Metrics collection task did not stop in time, cancelling..."
                )
                self._metrics_task.cancel()
                try:
                    await self._metrics_task
                except asyncio.CancelledError:
                    pass

        for deployment_name, client in self.clients.items():
            try:
                await client.close()
                logger.debug("Closed connection to %s", deployment_name)
            except Exception as e:
                logger.warning("Error closing connection to %s: %s", deployment_name, e)

        logger.info("Shutdown complete")

    async def run(self) -> None:
        from mcp.server.stdio import stdio_server

        try:
            async with stdio_server() as (read_stream, write_stream):
                logger.info("GoldenGate MCP Server starting...")
                await self.server.run(
                    read_stream,
                    write_stream,
                    self.server.create_initialization_options(),
                )
        finally:
            await self.shutdown()


async def main() -> None:
    load_dotenv()
    config = Config.from_env()
    server = GoldenGateMCPServer(config)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
