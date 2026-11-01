"""Deployment and process health summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from ..goldengate_client import process_states
from ..redaction import redact_error_text


class HealthMixin:
    """Requires: _get_client."""

    async def _get_deployment_health(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)

        health_data: Dict[str, Any] = {
            "deployment": deployment,
            "timestamp": datetime.utcnow().isoformat(),
            "overall_status": "healthy",
            "extracts": [],
            "replicats": [],
            "issues": [],
        }

        for process_type, group, label, get_lag in (
            ("extract", "extracts", "Extract", client.get_extract_lag),
            ("replicat", "replicats", "Replicat", client.get_replicat_lag),
        ):
            try:
                for info in await process_states(client, process_type):
                    status = info["status"]

                    if status == "running":
                        try:
                            info["lag"] = await get_lag(info["name"])
                        except Exception:
                            pass

                    health_data[group].append(info)

                    if status not in ["running", "stopped", "starting"]:
                        health_data["issues"].append(f"{label} {info['name']} is {status}")
                        if health_data["overall_status"] != "error":
                            health_data["overall_status"] = "degraded"
            except Exception as e:
                health_data["issues"].append(
                    redact_error_text(f"Failed to get {group}: {e}")
                )
                health_data["overall_status"] = "error"

        return health_data

    async def _check_process_errors(
        self, deployment: str, process_type: str, process_name: str
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.check_process_errors(process_type, process_name)

    async def _get_all_process_health(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_all_process_health()
