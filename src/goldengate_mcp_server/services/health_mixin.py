"""Deployment and process health summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


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

        try:
            extracts_data = await client.list_extracts()
            for extract in extracts_data.get("items", []):
                extract_name = extract.get("name")
                status = extract.get("status")

                extract_info: Dict[str, Any] = {
                    "name": extract_name,
                    "status": status,
                }

                if status == "running":
                    try:
                        lag_data = await client.get_extract_lag(extract_name)
                        extract_info["lag"] = lag_data
                    except Exception:
                        pass

                health_data["extracts"].append(extract_info)

                if status not in ["running", "stopped"]:
                    health_data["issues"].append(f"Extract {extract_name} is {status}")
                    health_data["overall_status"] = "degraded"
        except Exception as e:
            health_data["issues"].append(f"Failed to get extracts: {str(e)}")
            health_data["overall_status"] = "error"

        try:
            replicats_data = await client.list_replicats()
            for replicat in replicats_data.get("items", []):
                replicat_name = replicat.get("name")
                status = replicat.get("status")

                replicat_info: Dict[str, Any] = {
                    "name": replicat_name,
                    "status": status,
                }

                if status == "running":
                    try:
                        lag_data = await client.get_replicat_lag(replicat_name)
                        replicat_info["lag"] = lag_data
                    except Exception:
                        pass

                health_data["replicats"].append(replicat_info)

                if status not in ["running", "stopped"]:
                    health_data["issues"].append(f"Replicat {replicat_name} is {status}")
                    health_data["overall_status"] = "degraded"
        except Exception as e:
            health_data["issues"].append(f"Failed to get replicats: {str(e)}")
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
