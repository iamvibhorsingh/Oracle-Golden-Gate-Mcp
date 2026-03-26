"""Mutating GoldenGate operations (guarded by read_only)."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class WriteMixin:
    """Requires: config, _get_client."""

    config: Any

    async def _handle_write_operation(
        self, operation: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        if self.config.read_only:
            logger.warning("Write operation %s blocked: server in read-only mode", operation)
            return {
                "error": "Write operation blocked",
                "reason": "Server is running in read-only mode",
                "operation": operation,
            }

        deployment = arguments["deployment"]
        client = self._get_client(deployment)

        if operation == "start_extract":
            return await client.start_extract(arguments["extract_name"])
        if operation == "stop_extract":
            return await client.stop_extract(arguments["extract_name"])
        if operation == "start_replicat":
            return await client.start_replicat(arguments["replicat_name"])
        if operation == "stop_replicat":
            return await client.stop_replicat(arguments["replicat_name"])
        return {"error": f"Unknown write operation: {operation}"}
