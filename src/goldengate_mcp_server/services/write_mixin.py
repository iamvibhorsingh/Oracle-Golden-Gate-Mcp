"""Mutating GoldenGate operations (guarded by read_only)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from ..redaction import redact_error_text
from ..utils import unwrap_response

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
        if operation == "restart_process":
            return await client.restart_process(
                arguments["process_type"],
                arguments["process_name"],
                timeout=arguments.get("timeout_seconds", 120),
            )
        return {"error": f"Unknown write operation: {operation}"}

    async def _preview_write(self, operation: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """What a write tool would do to each process, from live status. Changes nothing."""
        client = self._get_client(arguments["deployment"])

        if "process_type" in arguments:
            process_type = arguments["process_type"]
        else:
            process_type = "extract" if operation.endswith("_extract") else "replicat"
        names: List[str] = arguments.get("process_names") or [
            arguments.get("process_name")
            or arguments.get("extract_name")
            or arguments.get("replicat_name")
        ]
        is_extract = process_type == "extract"
        read = client.get_extract_status if is_extract else client.get_replicat_status
        wanted = "stopped" if "stop" in operation else "running"

        async def target(name: str) -> Dict[str, Any]:
            try:
                status = unwrap_response(await read(name, use_cache=False)).get("status")
            except Exception as e:
                return {"name": name, "error": redact_error_text(str(e))}
            changes = operation == "restart_process" or status != wanted
            return {"name": name, "current_status": status, "would_change": changes}

        protected = arguments["deployment"] in self.config.protected_deployments
        return {
            "dry_run": True,
            "operation": operation,
            "deployment": arguments["deployment"],
            "process_type": process_type,
            "targets": list(await asyncio.gather(*(target(n) for n in names))),
            "change_ticket_required": protected,
            "note": "Nothing was changed. Repeat without dry_run to apply.",
        }
