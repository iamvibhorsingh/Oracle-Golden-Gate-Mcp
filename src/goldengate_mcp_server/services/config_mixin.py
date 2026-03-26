"""Backup / compare / process configuration tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ..goldengate_client import GoldenGateClient


class ConfigToolsMixin:
    """Requires: _get_client."""

    async def _backup_deployment_config(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.backup_all_configs()

    async def _get_process_config(
        self,
        deployment: str,
        process_name: str,
        process_type: str,
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)

        if process_type == "extract":
            return await client.get_extract_config(process_name)
        return await client.get_replicat_config(process_name)

    async def _compare_deployment_configs(
        self,
        deployment: str,
        backup_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)

        current_config = await client.backup_all_configs()

        if backup_file:
            backup_path = Path(backup_file)
            if not backup_path.exists():
                return {
                    "error": f"Backup file not found: {backup_file}",
                }

            with open(backup_path, encoding="utf-8") as f:
                backup_config = json.load(f)

            cmp_result = GoldenGateClient.compare_configs(current_config, backup_config)
            cmp_result["compared_with"] = backup_file
            cmp_result["backup_timestamp"] = backup_config.get("timestamp")
            cmp_result["current_timestamp"] = current_config.get("timestamp")

            return cmp_result

        return {
            "message": "Current configuration retrieved. Provide backup_file to compare.",
            "current_config": current_config,
        }
