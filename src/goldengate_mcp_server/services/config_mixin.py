"""Backup / compare / process configuration tools."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..goldengate_client import GoldenGateClient
from ..redaction import redact_sensitive_data


class ConfigToolsMixin:
    """Requires: config, _get_client."""

    config: Any

    def _backup_root(self) -> Path:
        return Path(self.config.backup_dir).resolve()

    @staticmethod
    def _snapshot_prefix(deployment: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", deployment) + "__"

    async def _backup_deployment_config(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.backup_all_configs()

    async def _save_config_snapshot(self, deployment: str) -> Dict[str, Any]:
        """Write a redacted snapshot under the backup directory (owner read/write only)."""
        client = self._get_client(deployment)
        snapshot = redact_sensitive_data(await client.backup_all_configs())
        snapshot["deployment"] = deployment

        root = self._backup_root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        path = root / f"{self._snapshot_prefix(deployment)}{stamp}.json"

        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)

        return {
            "backup_file": path.name,
            "deployment": deployment,
            "extracts": len(snapshot["extracts"]),
            "replicats": len(snapshot["replicats"]),
        }

    async def _list_config_backups(self, deployment: str) -> Dict[str, Any]:
        self._get_client(deployment)
        root = self._backup_root()
        files = sorted(root.glob(f"{self._snapshot_prefix(deployment)}*.json")) if root.is_dir() else []
        return {
            "deployment": deployment,
            "backups": [
                {"backup_file": p.name, "size_bytes": p.stat().st_size} for p in files
            ],
        }

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

        current_config = redact_sensitive_data(await client.backup_all_configs())

        if backup_file:
            # The path comes from the caller: never let it read outside the backup directory.
            root = self._backup_root()
            backup_path = (root / backup_file).resolve()
            if not backup_path.is_relative_to(root):
                return {"error": f"backup_file must be inside {self.config.backup_dir}"}
            if not backup_path.is_file():
                return {
                    "error": f"Backup file not found: {backup_file}",
                }

            with open(backup_path, encoding="utf-8") as f:
                backup_config = json.load(f)

            # Scoped callers must not read another deployment's snapshot through a diff.
            snapshot_of = backup_config.get("deployment")
            if snapshot_of and snapshot_of != deployment:
                return {"error": f"{backup_file} is a snapshot of {snapshot_of}, not {deployment}"}

            # Both sides redacted, so masked lines don't all show up as changes.
            backup_config = redact_sensitive_data(backup_config)
            cmp_result = GoldenGateClient.compare_configs(current_config, backup_config)
            cmp_result["compared_with"] = backup_file
            cmp_result["backup_timestamp"] = backup_config.get("timestamp")
            cmp_result["current_timestamp"] = current_config.get("timestamp")

            return cmp_result

        return {
            "message": "Current configuration retrieved. Provide backup_file to compare.",
            "current_config": current_config,
        }
